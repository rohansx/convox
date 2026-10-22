"""WebRtcLeg: browser tab as a leg, for dev loopback and the last-resort demo.

Despite the name this is plain websocket PCM, not WebRTC (static/loopback.html):
  browser -> us: binary = PCM16 LE mono 16 kHz (20 ms frames, any length accepted)
                 {"type": "hello", "side": "a", "lang": "hi"}   (optional, recorded)
                 {"type": "dtmf", "digit": "1"}                 (on-page buttons)
                 {"type": "mark", "name": tag}                  (playout reached our mark)
                 {"type": "telemetry", ...}                     (see below)
  us -> browser: binary = PCM16 LE mono 16 kHz to queue for playout
                 {"type": "mark", "name": tag}                  (ack it when played)
                 {"type": "clear"}                              (flush the playout queue)
                 {"type": "ping", "server_ms": n}               (every 5 s when recording)

Telemetry (for the call recorder, store/recorder.py). Any JSON message whose type is not
one of the above goes to the on_telemetry callbacks unchanged. The phone page sends
  {"type": "telemetry", "kind": "play_start" | "play_end" | "mic_level" | "ping",
   "tag"?: str,              the mark tag of the audio that started/ended playing, if known
   "client_ms": number,      the page's clock (performance.now()) when it happened
   "audio_ctx_ms"?: number,  AudioContext.currentTime * 1000 at that moment
   "rms"?: number}           mic_level: RMS of recent mic audio (0..1)
and answers each server ping at once with
  {"type": "telemetry", "kind": "pong", "server_ms": <the ping's>, "client_ms": number}
so the recorder can estimate the round trip and the page clock's offset (offset =
client_ms - (server_ms + rtt / 2), from the pong with the smallest rtt).

Marks fire from a playout estimate on the server clock (the browser plays at real time)
until the page has acked one; from then on acks win, and the estimate only fires a mark
if its ack is `ack_grace_ms` late. Not valid for latency claims (leg-transports.md 5).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from convox.clock import Clock, FakeClock, MonotonicClock
from convox.legs.ws import InboundAudio, PlayoutTracker, WSLike, receive_until
from convox.types import Side

log = logging.getLogger(__name__)


class WebRtcLeg:
    provider = "webrtc"

    def __init__(
        self, leg_id: Side, ws: WSLike, *, clock: Clock | None = None, ack_grace_ms: int = 1000
    ) -> None:
        self.leg_id = leg_id
        self.ws = ws
        self.clock: Clock = clock if clock is not None else MonotonicClock()
        self.ack_grace_ms = ack_grace_ms
        self.hello: dict[str, Any] = {}
        self.closed = False
        self.close_reason: str | None = None
        self.acks_seen = False
        self._inbound = InboundAudio()
        self._tracker = PlayoutTracker(
            self.clock, self._estimated_done, autopoll=not isinstance(self.clock, FakeClock)
        )
        self._pending: set[str] = set()
        self._mark_cbs: list[Callable[[str], None]] = []
        self._dtmf_cbs: list[Callable[[str], None]] = []
        self._telemetry_cbs: list[Callable[[dict[str, Any]], None]] = []
        self._ping_task: asyncio.Task[None] | None = None
        self._closed_evt = asyncio.Event()
        self._lock = asyncio.Lock()

    def frames(self) -> AsyncIterator[bytes]:
        return self._inbound.frames()

    async def send(self, pcm16_16k: bytes) -> None:
        if self.closed or not pcm16_16k:
            return
        async with self._lock:
            self._tracker.played(len(pcm16_16k))
            await self._send(self.ws.send_bytes, pcm16_16k)

    async def mark(self, tag: str) -> None:
        if self.closed:
            return
        async with self._lock:
            self._pending.add(tag)
            self._tracker.mark(tag)
            await self._send(self.ws.send_text, json.dumps({"type": "mark", "name": tag}))

    async def clear(self) -> None:
        if self.closed:
            return
        async with self._lock:
            self._pending.clear()
            self._tracker.clear()
            await self._send(self.ws.send_text, json.dumps({"type": "clear"}))

    def poll(self) -> None:
        """Fire estimated marks that are due (call after advancing a FakeClock)."""
        self._tracker.poll()

    def on_mark(self, cb: Callable[[str], None]) -> None:
        self._mark_cbs.append(cb)

    def on_dtmf(self, cb: Callable[[str], None]) -> None:
        self._dtmf_cbs.append(cb)

    def on_telemetry(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """cb(msg) for every JSON message of a type this leg does not handle itself."""
        self._telemetry_cbs.append(cb)

    def start_pings(
        self,
        now_ms: Callable[[], int],
        *,
        interval_s: float = 5.0,
        on_ping: Callable[[int], None] | None = None,
    ) -> None:
        """Send {"type": "ping", "server_ms": now_ms()} every interval_s until close."""
        if self._ping_task is None and not self.closed:
            self._ping_task = asyncio.create_task(self._ping_loop(now_ms, interval_s, on_ping))

    async def _ping_loop(
        self, now_ms: Callable[[], int], interval_s: float, on_ping: Callable[[int], None] | None
    ) -> None:
        while not self.closed:
            server_ms = now_ms()
            async with self._lock:
                msg = json.dumps({"type": "ping", "server_ms": server_ms})
                await self._send(self.ws.send_text, msg)
            if on_ping is not None:
                on_ping(server_ms)
            await asyncio.sleep(interval_s)

    async def ping(self, server_ms: int) -> None:
        """Ask the page for {"type":"telemetry","kind":"pong","server_ms","client_ms"}."""
        if self.closed:
            return
        async with self._lock:
            msg = json.dumps({"type": "ping", "server_ms": server_ms})
            await self._send(self.ws.send_text, msg)

    async def run(self) -> None:
        try:
            while not self.closed:
                msg = await receive_until(self.ws.receive(), self._closed_evt)
                if msg is None or msg.get("type") == "websocket.disconnect":
                    self.close_reason = self.close_reason or "disconnect"
                    break
                if msg.get("bytes") is not None:
                    data = msg["bytes"]
                    self._inbound.push(data[: len(data) // 2 * 2])
                elif msg.get("text") is not None:
                    self._handle_text(msg["text"])
        finally:
            await self.close()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.close_reason = self.close_reason or "closed"
        self._closed_evt.set()
        if self._ping_task is not None and self._ping_task is not asyncio.current_task():
            self._ping_task.cancel()
        self._pending.clear()
        self._tracker.close()
        self._inbound.end()
        try:
            await self.ws.close()
        except Exception:
            pass
        log.info(
            "leg.close",
            extra={
                "side": self.leg_id,
                "provider": self.provider,
                "close_reason": self.close_reason,
            },
        )

    def _handle_text(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "dtmf" and msg.get("digit") is not None:
            for cb in self._dtmf_cbs:
                cb(str(msg["digit"]))
        elif kind == "mark" and msg.get("name") is not None:
            self.acks_seen = True
            self._fire(str(msg["name"]))
        elif kind == "hello":
            self.hello = msg
        elif kind not in ("dtmf", "mark"):
            for tcb in self._telemetry_cbs:
                try:
                    tcb(msg)
                except Exception:  # a telemetry consumer must never take the leg down
                    log.exception("leg.telemetry_error", extra={"side": self.leg_id})

    def _estimated_done(self, tag: str) -> None:
        if tag not in self._pending:
            return
        if not self.acks_seen:
            self._fire(tag)
        elif self.ack_grace_ms <= 0:
            asyncio.get_running_loop().call_soon(self._fire, tag)
        else:
            asyncio.get_running_loop().call_later(self.ack_grace_ms / 1000, self._fire, tag)

    def _fire(self, tag: str) -> None:
        if tag not in self._pending:
            return
        self._pending.discard(tag)
        for cb in self._mark_cbs:
            cb(tag)

    async def _send(self, fn: Callable[[Any], Any], data: Any) -> None:
        try:
            await fn(data)
        except Exception as e:
            log.warning("leg.send_failed", extra={"side": self.leg_id, "error": repr(e)})
            self.close_reason = self.close_reason or "disconnect"
            self._closed_evt.set()
