"""RecordingLeg: wraps any LegTransport and copies what passes through it to a
CallRecorder (store/recorder.py): inbound frames (the mic), send() (what the leg is told
to play), clear/mark/mark acks/DTMF as events, and, for a leg with telemetry (WebRtcLeg),
the page's telemetry and server pings for the clock offset.

Transparent: every call is forwarded unchanged and in the same order, and attributes the
API reads off legs (close_reason, started, stream_sid, ...) come from the inner leg. Only
the session sees the wrapper; the websocket pump (`run()`) stays on the inner leg.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from dubhash.legs.base import LegTransport
from dubhash.store.recorder import CallRecorder
from dubhash.types import Side

PING_INTERVAL_S = 5.0


class RecordingLeg:
    def __init__(
        self,
        inner: LegTransport,
        recorder: CallRecorder,
        side: Side | None = None,
        *,
        ping_interval_s: float = PING_INTERVAL_S,
    ) -> None:
        self.inner = inner
        self.leg_id: Side = inner.leg_id
        self._rec = recorder
        self._side: Side = side or inner.leg_id
        s = self._side
        inner.on_mark(lambda tag: recorder.note("leg.mark_ack", {"side": s, "tag": tag}))
        inner.on_dtmf(lambda digit: recorder.note("leg.dtmf", {"side": s, "digit": digit}))
        on_telemetry = getattr(inner, "on_telemetry", None)
        if on_telemetry is not None:
            on_telemetry(lambda msg: recorder.telemetry(s, msg))
        start_pings = getattr(inner, "start_pings", None)
        if start_pings is not None:
            start_pings(
                recorder.clock.now_ms,
                interval_s=ping_interval_s,
                on_ping=lambda ms: recorder.ping_sent(s, ms),
            )

    async def frames(self) -> AsyncIterator[bytes]:
        async for frame in self.inner.frames():
            self._rec.mic(self._side, frame)
            yield frame

    async def send(self, pcm16_16k: bytes) -> None:
        self._rec.sent(self._side, pcm16_16k)
        await self.inner.send(pcm16_16k)

    async def clear(self) -> None:
        self._rec.cleared(self._side)
        await self.inner.clear()

    async def mark(self, tag: str) -> None:
        self._rec.note("leg.mark", {"side": self._side, "tag": tag})
        await self.inner.mark(tag)

    def on_mark(self, cb: Callable[[str], None]) -> None:
        self.inner.on_mark(cb)

    def on_dtmf(self, cb: Callable[[str], None]) -> None:
        self.inner.on_dtmf(cb)

    async def close(self) -> None:
        await self.inner.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)
