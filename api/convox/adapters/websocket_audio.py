"""Audio WebSocket adapter — bidirectional PCM against a real voice agent.

Where the text adapter exchanges JSON utterances, this one streams binary PCM16
frames in both directions and derives turn boundaries from the audio itself. That
is the whole point: an agent's reply is not a string that arrives instantly, it is
sound that starts at some moment, lasts some duration, and can be interrupted. All
of the measurements that transcript-based tools cannot make — response latency
from the caller's ear, barge-in stop time, TTS truncation — need this channel.

Protocol
--------
Binary frames are PCM16 mono at the negotiated sample rate, 20 ms per frame.
JSON frames carry control and side-channel data::

    caller → agent   {"type": "session.start", "audio": {...}}
                     {"type": "caller.dtmf", "digits": "1"}
                     {"type": "caller.hangup"}

    agent  → caller  {"type": "agent.tool_call", "name": "...", "args": {...}}
                     {"type": "agent.transcript", "text": "..."}   (optional)
                     {"type": "agent.hangup"}

An agent that sends no transcript is fully supported — Convox transcribes the
incoming audio itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from convox.adapters.base import (
    AdapterError,
    AgentEvent,
    TargetAdapter,
    TargetSession,
    TrialContext,
)
from convox.audio import pcm
from convox.model.target import AdapterCapabilities

_CLOSED = object()


class AudioWebSocketSession(TargetSession):
    def __init__(
        self,
        ws: ClientConnection,
        ctx: TrialContext,
        capabilities: AdapterCapabilities,
        audio_format: pcm.AudioFormat,
    ) -> None:
        super().__init__(ctx, capabilities)
        self._ws = ws
        self.audio_format = audio_format
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._reader = asyncio.create_task(self._read_loop())
        self._closed = False

    # ── outbound ──

    async def send_audio(self, frame: bytes) -> None:
        try:
            await self._ws.send(frame)
        except websockets.exceptions.WebSocketException as exc:
            raise AdapterError(f"audio send failed: {exc}") from exc

    async def _send_json(self, payload: dict[str, Any]) -> None:
        payload.setdefault("at_ms", self.ctx.clock.now_ms())
        try:
            await self._ws.send(json.dumps(payload))
        except websockets.exceptions.WebSocketException as exc:
            raise AdapterError(f"send failed: {exc}") from exc

    async def say(self, text: str, *, interrupting: bool = False) -> None:
        """Not used on an audio channel — the caller pipeline streams frames."""
        raise AdapterError("audio targets are driven by streamed frames, not say()")

    async def send_dtmf(self, digits: str, *, tone_ms: int = 120, gap_ms: int = 80) -> None:
        await self._send_json({"type": "caller.dtmf", "digits": digits, "tone_ms": tone_ms})

    async def hangup(self) -> None:
        with contextlib.suppress(AdapterError):
            await self._send_json({"type": "caller.hangup"})

    # ── inbound ──

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                at_ms = self.ctx.clock.now_ms()
                if isinstance(raw, bytes):
                    await self._queue.put(AgentEvent("agent.audio", at_ms, {"pcm": raw}))
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._queue.put(AgentEvent("error", at_ms, {"message": "malformed JSON frame"}))
                    continue
                kind = str(msg.get("type", "unknown"))
                payload = {k: v for k, v in msg.items() if k not in ("type", "at_ms")}
                await self._queue.put(AgentEvent(kind, int(msg.get("at_ms", at_ms)), payload))
        except websockets.exceptions.ConnectionClosedOK:
            pass
        except websockets.exceptions.WebSocketException as exc:
            await self._queue.put(
                AgentEvent("error", self.ctx.clock.now_ms(), {"message": f"connection error: {exc}"})
            )
        finally:
            await self._queue.put(_CLOSED)

    async def events(self) -> AsyncIterator[AgentEvent]:
        while True:
            item = await self._queue.get()
            if item is _CLOSED:
                return
            yield item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._reader
        with contextlib.suppress(Exception):
            await self._ws.close()


class AudioWebSocketAdapter(TargetAdapter):
    kind = "websocket_audio"
    capabilities = AdapterCapabilities(
        audio=True,
        agent_transcript=False,  # optional; Convox transcribes the audio itself
        tool_calls=True,
        dtmf=True,
        recording=True,
        traces=False,
        inbound=False,
        barge_in=True,
    )

    async def connect(self, ctx: TrialContext) -> TargetSession:
        url = self.config.get("url")
        if not url:
            raise AdapterError("websocket_audio target requires a `url`")

        audio_format = pcm.AudioFormat(
            sample_rate=int(self.config.get("sample_rate", 16000)),
            frame_ms=int(self.config.get("frame_ms", 20)),
        )

        try:
            ws = await asyncio.wait_for(
                websockets.connect(
                    url,
                    additional_headers=self.config.get("headers") or {},
                    open_timeout=None,
                    max_size=None,
                ),
                timeout=self.config.get("connect_timeout_s", 10),
            )
        except TimeoutError as exc:
            raise AdapterError(f"timed out connecting to {url}") from exc
        except OSError as exc:
            raise AdapterError(f"could not connect to {url}: {exc}") from exc
        except websockets.exceptions.WebSocketException as exc:
            raise AdapterError(f"handshake failed for {url}: {exc}") from exc

        ctx.clock.start()
        session = AudioWebSocketSession(ws, ctx, self.capabilities, audio_format)
        await session._send_json(
            {
                "type": "session.start",
                "audio": {
                    "sample_rate": audio_format.sample_rate,
                    "frame_ms": audio_format.frame_ms,
                    "encoding": "pcm16",
                },
                "variables": ctx.dynamic_variables,
            }
        )
        return session
