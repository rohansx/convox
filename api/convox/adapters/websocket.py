"""WebSocket target adapter — the universal fallback.

Speaks a small JSON protocol that any agent can implement in an afternoon, which
makes it the adapter of last resort for custom stacks and the one we test
ourselves against.

Caller → agent::

    {"type": "caller.text",   "text": "...", "at_ms": 1234}
    {"type": "caller.dtmf",   "digits": "1", "at_ms": 1234}
    {"type": "caller.hangup", "at_ms": 1234}

Agent → caller::

    {"type": "agent.speech_start"}
    {"type": "agent.text", "text": "...", "final": true}
    {"type": "agent.speech_end"}
    {"type": "agent.tool_call", "name": "...", "args": {...}}
    {"type": "agent.hangup"}

Only ``agent.text`` is required; speech markers are synthesised from message
arrival when absent. Because this channel carries text rather than audio, the
adapter declares ``audio=False`` and audio-layer assertions correctly report
``unsupported`` instead of passing by default.
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
from convox.model.target import AdapterCapabilities

_CLOSED = object()


class WebSocketSession(TargetSession):
    def __init__(
        self,
        ws: ClientConnection,
        ctx: TrialContext,
        capabilities: AdapterCapabilities,
    ) -> None:
        super().__init__(ctx, capabilities)
        self._ws = ws
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._reader = asyncio.create_task(self._read_loop())
        self._closed = False

    # ── outbound ──

    async def _send(self, payload: dict[str, Any]) -> None:
        payload.setdefault("at_ms", self.ctx.clock.now_ms())
        try:
            await self._ws.send(json.dumps(payload))
        except websockets.exceptions.WebSocketException as exc:
            raise AdapterError(f"send failed: {exc}") from exc

    async def say(self, text: str, *, interrupting: bool = False) -> None:
        await self._send({"type": "caller.text", "text": text, "interrupting": interrupting})

    async def send_dtmf(self, digits: str, *, tone_ms: int = 120, gap_ms: int = 80) -> None:
        await self._send({"type": "caller.dtmf", "digits": digits, "tone_ms": tone_ms, "gap_ms": gap_ms})

    async def hangup(self) -> None:
        with contextlib.suppress(AdapterError):
            await self._send({"type": "caller.hangup"})

    # ── inbound ──

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    # Audio frames are accepted but not yet interpreted; recorded
                    # so the transport stays forward-compatible.
                    await self._queue.put(
                        AgentEvent("agent.audio", self.ctx.clock.now_ms(), {"bytes": len(raw)})
                    )
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._queue.put(
                        AgentEvent("error", self.ctx.clock.now_ms(), {"message": "malformed JSON frame"})
                    )
                    continue
                await self._queue.put(self._to_event(msg))
        except websockets.exceptions.ConnectionClosedOK:
            pass
        except websockets.exceptions.WebSocketException as exc:
            await self._queue.put(
                AgentEvent("error", self.ctx.clock.now_ms(), {"message": f"connection error: {exc}"})
            )
        finally:
            await self._queue.put(_CLOSED)

    def _to_event(self, msg: dict[str, Any]) -> AgentEvent:
        at_ms = int(msg.get("at_ms", self.ctx.clock.now_ms()))
        kind = str(msg.get("type", "unknown"))
        payload = {k: v for k, v in msg.items() if k not in ("type", "at_ms")}
        return AgentEvent(kind, at_ms, payload)

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


class WebSocketAdapter(TargetAdapter):
    kind = "websocket"
    capabilities = AdapterCapabilities(
        audio=False,
        agent_transcript=True,
        tool_calls=True,
        dtmf=True,
        recording=False,
        traces=False,
        inbound=False,
        barge_in=False,
    )

    async def connect(self, ctx: TrialContext) -> TargetSession:
        url = self.config.get("url")
        if not url:
            raise AdapterError("websocket target requires a `url`")
        headers = self.config.get("headers") or {}
        try:
            ws = await asyncio.wait_for(
                websockets.connect(url, additional_headers=headers, open_timeout=None),
                timeout=self.config.get("connect_timeout_s", 10),
            )
        except TimeoutError as exc:
            raise AdapterError(f"timed out connecting to {url}") from exc
        except OSError as exc:
            raise AdapterError(f"could not connect to {url}: {exc}") from exc
        except websockets.exceptions.WebSocketException as exc:
            raise AdapterError(f"handshake failed for {url}: {exc}") from exc

        ctx.clock.start()
        session = WebSocketSession(ws, ctx, self.capabilities)
        if ctx.dynamic_variables:
            await session._send({"type": "caller.variables", "variables": ctx.dynamic_variables})
        return session
