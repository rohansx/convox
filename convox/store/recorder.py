"""CallRecorder: everything needed to diagnose one call afterwards, on disk.

    <base_dir>/<YYYY-MM-DD>/<call_id>/
      events.jsonl   every CallEvent of the call (from the EventBus) plus the leg taps
                     (leg.mark, leg.mark_ack, leg.clear, leg.dtmf, leg.ping) and a first
                     "rec.meta" line: {"ts": wall-clock ISO, "ms": call-relative, "kind",
                     "payload"}
      client.jsonl   the phone page's telemetry (legs/webrtc.py), plus `side`, the server
                     receive time `rx_ms` and, for a pong, `rtt_ms` and `offset_ms`
      a_in.wav       what leg A's mic sent (PCM16 mono 16 kHz)
      b_in.wav       what leg B's mic sent
      a_out.wav      what was sent to leg A, placed where it played
      b_out.wav      what was sent to leg B
      summary.json   pair, mode, duration, per-turn first audio, p50/p95, stage means and
                     warnings (store/timeline.py); read it with tools/call_report.py

All four wavs start at call-relative 0 ms (the session clock, the same clock as the turn
log's t_* fields) and are padded to the same length at the end, so they line up in
Audacity. Mic frames are placed at their arrival time (a gap longer than MIC_SLACK_MS is
padded with silence). Outbound audio is placed on a playout cursor, as the leg plays it:
a send() while earlier audio is still playing queues after it, a send() after a quiet spell
starts at the current time, and clear() drops what had not played yet.

Dev and test only (settings.call_recording): this stores both callers' voices, so it
needs their consent and is never on by default in prod (DPDP). Transcript text is kept
only when the call has record=true.

Nothing here blocks the hot path: the tap methods append to memory, and one background
task flushes to disk every `flush_interval_s` (file writes run in a worker thread) and at
close(). Each wav's unflushed buffer is bounded by `max_buffer_s`; past that audio is
dropped and the summary says so.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import struct
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from convox.audio.frames import SAMPLE_RATE, SAMPLE_WIDTH
from convox.clock import Clock
from convox.events import EventBus
from convox.store.timeline import analyze
from convox.store.writer import jsonable
from convox.types import CallEvent

log = logging.getLogger(__name__)

BYTES_PER_MS = SAMPLE_RATE * SAMPLE_WIDTH // 1000  # 32
MIC_SLACK_MS = 60  # mic frames arriving this much late are appended, not padded before
TRACKS = ("a_in", "b_in", "a_out", "b_out")
BUS_DRAIN_TIMEOUT_S = 2.0
_PAD_CHUNK = 1 << 20


def _pos(ms: int) -> int:
    return max(0, ms) * BYTES_PER_MS


class _Track:
    """One wav's timeline: `flushed` bytes are on disk, `buf` follows them in memory."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.flushed = 0
        self.buf = bytearray()
        self.dropped = 0  # bytes

    @property
    def end(self) -> int:
        return self.flushed + len(self.buf)

    def put(self, start: int, pcm: bytes) -> None:
        """Write pcm at byte `start` (>= end; the gap is silence)."""
        pad = max(0, start - self.end)
        if len(self.buf) + pad + len(pcm) > self.cap:
            self.dropped += pad + len(pcm)
            return
        if pad:
            self.buf += bytes(pad)
        self.buf += pcm

    def truncate(self, at: int) -> int:
        """Drop unflushed audio after byte `at`; returns the bytes dropped."""
        cut = max(at, self.flushed)
        dropped = max(0, self.end - cut)
        if dropped:
            del self.buf[cut - self.flushed :]
        return dropped

    def take(self, upto: int) -> bytes:
        n = max(0, min(upto, self.end) - self.flushed)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        self.flushed += n
        return out


def _wav_header(data_bytes: int) -> bytes:
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_bytes)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * SAMPLE_WIDTH, 2, 16)
        + b"data"
        + struct.pack("<I", data_bytes)
    )


class CallRecorder:
    def __init__(
        self,
        call_id: str,
        base_dir: Path | str,
        clock: Clock,
        *,
        meta: dict[str, Any] | None = None,
        record_text: bool = True,
        flush_interval_s: float = 1.0,
        max_buffer_s: float = 60.0,
        date: str | None = None,
        wall_time: Any = time.time,
    ) -> None:
        self.call_id = call_id
        self.clock = clock
        self.record_text = record_text
        self.flush_interval_s = flush_interval_s
        now = wall_time()
        self._t0_wall = datetime.fromtimestamp(now, UTC) - timedelta(milliseconds=clock.now_ms())
        day = date or datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        self.dir = Path(base_dir) / day / call_id
        cap = int(max_buffer_s * 1000) * BYTES_PER_MS
        self._tracks = {name: _Track(cap) for name in TRACKS}
        self._events: list[dict[str, Any]] = []  # everything, for the summary
        self._client: list[dict[str, Any]] = []
        self._pending_events: list[str] = []
        self._pending_client: list[str] = []
        self._meta = dict(meta or {})
        self._closed = False
        self._lock = asyncio.Lock()
        self._flusher: asyncio.Task[None] | None = None
        self._bus_task: asyncio.Task[None] | None = None
        self._ping_ms: dict[str, int] = {}
        self.note("rec.meta", {"call_id": call_id, **self._meta})

    # --- lifecycle --------------------------------------------------------------------

    async def start(self, bus: EventBus | None = None) -> None:
        """Start the flusher and, with a bus, record this call's events from now on."""
        self._flusher = asyncio.create_task(self._flush_loop(), name=f"rec-{self.call_id}")
        if bus is not None:
            self._bus_task = asyncio.create_task(self._consume(bus), name=f"rec-bus-{self.call_id}")
            # let the subscriber register before the caller publishes anything
            for _ in range(2):
                await asyncio.sleep(0)

    async def close(self) -> None:
        """Wait (briefly) for the call's last events, flush everything, write summary.json."""
        if self._closed:
            return
        if self._bus_task is not None:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(self._bus_task), BUS_DRAIN_TIMEOUT_S)
            self._bus_task.cancel()
        self._closed = True
        if self._flusher is not None:
            self._flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flusher
        try:
            await self._flush(final=True)
            summary = self.summary()
            await asyncio.to_thread(self._write_summary, summary)
        except Exception:
            log.exception("recorder.close_failed", extra={"call_id": self.call_id})

    # --- taps (hot path: memory only) ---------------------------------------------------

    def event(self, ev: CallEvent) -> None:
        if self._closed or ev.call_id != self.call_id:
            return
        payload = jsonable(ev.payload)
        if not self.record_text:
            payload = _strip_text(ev.kind, payload)
        self._add_event(ev.ts_ms, ev.kind, payload)

    def note(self, kind: str, payload: dict[str, Any]) -> None:
        """A recorder-side event (leg taps) at the current call time."""
        if self._closed:
            return
        self._add_event(self.clock.now_ms(), kind, jsonable(payload))

    def mic(self, side: str, pcm: bytes) -> None:
        if self._closed or not pcm:
            return
        tr = self._tracks[f"{side}_in"]
        start = _pos(self.clock.now_ms()) - len(pcm)
        if start <= tr.end + _pos(MIC_SLACK_MS):
            start = tr.end
        tr.put(start, pcm)

    def sent(self, side: str, pcm: bytes) -> None:
        if self._closed or not pcm:
            return
        tr = self._tracks[f"{side}_out"]
        tr.put(max(tr.end, _pos(self.clock.now_ms())), pcm)

    def cleared(self, side: str) -> None:
        if self._closed:
            return
        dropped = self._tracks[f"{side}_out"].truncate(_pos(self.clock.now_ms()))
        self.note("leg.clear", {"side": side, "dropped_ms": dropped // BYTES_PER_MS})

    def ping_sent(self, side: str, server_ms: int) -> None:
        self._ping_ms[side] = server_ms
        self.note("leg.ping", {"side": side, "server_ms": server_ms})

    def telemetry(self, side: str, msg: dict[str, Any]) -> None:
        if self._closed:
            return
        now = self.clock.now_ms()
        line: dict[str, Any] = {"ts": self._iso(now), "rx_ms": now, "side": side}
        line.update(jsonable({k: v for k, v in msg.items() if k not in line}))
        if msg.get("kind") == "pong":
            try:
                server_ms, client_ms = int(msg["server_ms"]), float(msg["client_ms"])
                rtt = now - server_ms
                line["rtt_ms"] = rtt
                line["offset_ms"] = round(client_ms - (server_ms + rtt / 2))
            except (KeyError, TypeError, ValueError):
                pass
        self._client.append(line)
        self._pending_client.append(json.dumps(line, ensure_ascii=False))

    def buffered_bytes(self, track: str) -> int:
        return len(self._tracks[track].buf)

    # --- summary -----------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        result = analyze(self._events, self._client)
        spoken = [r for r in result["turns"] if not r["backchannel"]]
        warnings = list(result["warnings"])
        for name, tr in self._tracks.items():
            if tr.dropped:
                warnings.append(
                    {
                        "code": "recording_dropped",
                        "side": name[0],
                        "seq": None,
                        "detail": f"{name}: {tr.dropped // BYTES_PER_MS} ms of audio not "
                        "recorded (disk too slow)",
                    }
                )
        end = None
        for e in reversed(self._events):
            if e["kind"] == "call" and e["payload"].get("reason") is not None:
                end = e["payload"]
                break
        return {
            "call_id": self.call_id,
            "pair": self._meta.get("pair"),
            "mode": self._meta.get("mode"),
            "sides": self._meta.get("sides"),
            "duration_ms": self.clock.now_ms(),
            "end_reason": end.get("reason") if end else None,
            "turns": len(spoken),
            "backchannels": len(result["turns"]) - len(spoken),
            "first_audio": result["first_audio"],
            "heard": result["heard"],
            # L9, per utterance: what the listener felt from when the speaker started
            "speech_start_to_first_heard": result["speech_start_to_first_heard"],
            "ear_voice_span": result["ear_voice_span"],
            "utterance_first_audio": result["utterance_first_audio"],
            "heard_from_start": result["heard_from_start"],
            "stages": result["stages"],
            "clock": result["clock"],
            "langs": result["langs"],
            "per_turn": [
                {
                    k: r[k]
                    for k in (
                        "side",
                        "seq",
                        "backchannel",
                        "speech_start",
                        "speech_end",
                        "play_start",
                        "client_play_start",
                        "first_audio_ms",
                        "heard_ms",
                        "gaps",
                        "clipped_ms",
                    )
                }
                for r in result["turns"]
            ],
            "warnings": warnings,
        }

    # --- internals -----------------------------------------------------------------------

    def _iso(self, ms: int) -> str:
        t = self._t0_wall + timedelta(milliseconds=ms)
        return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _add_event(self, ms: int, kind: str, payload: Any) -> None:
        line = {"ts": self._iso(ms), "ms": ms, "kind": kind, "payload": payload}
        self._events.append(line)
        self._pending_events.append(json.dumps(line, ensure_ascii=False))

    async def _consume(self, bus: EventBus) -> None:
        async for ev in bus.subscribe(self.call_id):
            self.event(ev)
            if ev.kind == "call" and ev.payload.get("status") == "ended":
                return

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval_s)
            try:
                await self._flush(final=False)
            except Exception:
                log.exception("recorder.flush_failed", extra={"call_id": self.call_id})

    async def _flush(self, *, final: bool) -> None:
        async with self._lock:
            now = _pos(self.clock.now_ms())
            chunks: dict[str, bytes] = {}
            for name, tr in self._tracks.items():
                # outbound audio past "now" has not played yet: a clear() may still drop it
                upto = tr.end if final or name.endswith("_in") else min(tr.end, now)
                chunks[name] = tr.take(upto)
            length = max([now, *(tr.end for tr in self._tracks.values())]) if final else None
            events, self._pending_events = self._pending_events, []
            client, self._pending_client = self._pending_client, []
            await asyncio.to_thread(self._write, events, client, chunks, length)

    def _write(
        self,
        events: list[str],
        client: list[str],
        chunks: dict[str, bytes],
        final_length: int | None,
    ) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for name, rows in (("events.jsonl", events), ("client.jsonl", client)):
            path = self.dir / name
            if rows or final_length is not None:
                with path.open("a", encoding="utf-8") as fh:
                    fh.writelines(r + "\n" for r in rows)
        for name, data in chunks.items():
            path = self.dir / f"{name}.wav"
            if not path.exists():
                path.write_bytes(_wav_header(0))
            with path.open("r+b") as fh:
                fh.seek(0, 2)
                fh.write(data)
                if final_length is not None:
                    size = fh.tell() - 44
                    while size < final_length:
                        n = min(_PAD_CHUNK, final_length - size)
                        fh.write(bytes(n))
                        size += n
                    fh.seek(0)
                    fh.write(_wav_header(size))

    def _write_summary(self, summary: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class RecorderBook:
    """One CallRecorder per live call (both legs share it), closed when the call ends.
    Lives on app.state; the leg websocket handler asks it for the call's recorder."""

    def __init__(self, base_dir: Path | str, bus: EventBus | None) -> None:
        self.base_dir = Path(base_dir)
        self.bus = bus
        self._recs: dict[str, CallRecorder] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def get(self, call_id: str) -> CallRecorder | None:
        return self._recs.get(call_id)

    async def recorder_for(self, session: Any) -> CallRecorder:
        call_id = session.call_id
        rec = self._recs.get(call_id)
        if rec is not None:
            return rec
        rec = CallRecorder(
            call_id,
            self.base_dir,
            session.clock,
            meta=session_meta(session),
            record_text=bool(getattr(session, "record", False)),
        )
        self._recs[call_id] = rec
        await rec.start(self.bus)
        task = asyncio.create_task(self._finish(session, rec), name=f"rec-end-{call_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        log.info("recorder.start", extra={"call_id": call_id, "path": str(rec.dir)})
        return rec

    async def _finish(self, session: Any, rec: CallRecorder) -> None:
        try:
            await session.wait_closed()
        finally:
            await rec.close()
            self._recs.pop(rec.call_id, None)
            log.info("recorder.done", extra={"call_id": rec.call_id, "path": str(rec.dir)})

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


def session_meta(session: Any) -> dict[str, Any]:
    pair = getattr(session, "pair", None)
    sides: dict[str, Any] = {}
    for s in ("a", "b"):
        cfg = pair.side(s) if pair is not None else None
        if cfg is not None:
            sides[s] = {
                "lang": cfg.lang,
                "stt": cfg.stt,
                "stt_lang": getattr(cfg, "mt_lang", cfg.lang),
                "tts": cfg.tts,
            }
    hints = getattr(getattr(session, "auto", None), "hints", None) or {}
    for s, hint in hints.items():  # lang "auto": the side's lang above is where it started
        if s in sides:
            sides[s].update({"auto": True, "hint": hint})
    meta: dict[str, Any] = {
        "pair": getattr(pair, "key", None),
        "mode": getattr(session, "mode", None),
        "record": bool(getattr(session, "record", False)),
        "sides": sides,
    }
    for k in ("eou_ms", "early_final_ms", "barge_in_min_ms", "duck_db"):
        if pair is not None and hasattr(pair, k):
            meta[k] = getattr(pair, k)
    return meta


def _strip_text(kind: str, payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if kind == "partial":
        return {k: v for k, v in payload.items() if k != "text"}
    if kind == "turn":
        turn = payload.get("turn")
        if isinstance(turn, dict):
            return {**payload, "turn": {**turn, "src_text": None, "tgt_text": None}}
    return payload
