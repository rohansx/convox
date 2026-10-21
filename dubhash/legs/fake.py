"""FakeLeg: in-process leg for tests and the bench. PCM16@16k in (as fast as consumed, or
paced at real time), outbound captured to `outbound` for writing to a wav, marks fired
after a simulated playout on the given clock, DTMF injected by the test."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from dubhash.audio.frames import FRAME_MS
from dubhash.clock import Clock, FakeClock, MonotonicClock
from dubhash.legs.ws import InboundAudio, PlayoutTracker
from dubhash.types import Side


class FakeLeg:
    def __init__(
        self,
        leg_id: Side,
        inbound: bytes = b"",
        *,
        clock: Clock | None = None,
        realtime: bool = False,
        playout: bool = True,
        advance_clock: bool = False,
        on_frame: Callable[[int], None] | None = None,
    ) -> None:
        """realtime: yield one frame per 20 ms of wall time. playout: marks wait for the
        sent audio to "play" at 16 kHz on `clock` (else they fire on the next loop turn).
        advance_clock: advance the FakeClock 20 ms before each frame and fire due marks.
        on_frame(index) runs before each frame is yielded."""
        if advance_clock and not isinstance(clock, FakeClock):
            raise ValueError("advance_clock needs a FakeClock")
        self.leg_id = leg_id
        self.clock: Clock = clock if clock is not None else MonotonicClock()
        self.realtime = realtime
        self.playout = playout
        self.advance_clock = advance_clock
        self.log: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False
        self._on_frame = on_frame
        self._inbound = InboundAudio()
        self._outbound = bytearray()
        self._mark_cbs: list[Callable[[str], None]] = []
        self._dtmf_cbs: list[Callable[[str], None]] = []
        self._tracker = PlayoutTracker(
            self.clock, self._fire_mark, autopoll=not isinstance(self.clock, FakeClock)
        )
        if inbound:
            self._inbound.push(inbound)

    # inbound

    def feed(self, pcm16_16k: bytes) -> None:
        self._inbound.push(pcm16_16k)

    def end_input(self) -> None:
        self._inbound.end()

    async def frames(self) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        i = 0
        async for frame in self._inbound.frames():
            if self.realtime:
                delay = t0 + i * FRAME_MS / 1000 - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
            if self.advance_clock:
                assert isinstance(self.clock, FakeClock)
                self.clock.advance(FRAME_MS)
                self._tracker.poll()
            if self._on_frame is not None:
                self._on_frame(i)
            yield frame
            i += 1

    # outbound

    @property
    def outbound(self) -> bytes:
        return bytes(self._outbound)

    async def send(self, pcm16_16k: bytes) -> None:
        self.log.append(("send", (len(pcm16_16k),)))
        self._outbound += pcm16_16k
        if self.playout:
            self._tracker.played(len(pcm16_16k))

    async def clear(self) -> None:
        dropped = min(self._tracker.clear(), len(self._outbound))
        if dropped:
            del self._outbound[len(self._outbound) - dropped :]
        self.log.append(("clear", (dropped,)))

    async def mark(self, tag: str) -> None:
        self.log.append(("mark", (tag,)))
        self._tracker.mark(tag)

    def poll(self) -> None:
        """Fire marks whose audio has played by now (call after advancing a FakeClock)."""
        self._tracker.poll()

    def on_mark(self, cb: Callable[[str], None]) -> None:
        self._mark_cbs.append(cb)

    def on_dtmf(self, cb: Callable[[str], None]) -> None:
        self._dtmf_cbs.append(cb)

    def inject_dtmf(self, digit: str) -> None:
        self.log.append(("dtmf", (digit,)))
        for cb in self._dtmf_cbs:
            cb(digit)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.log.append(("close", ()))
        self._tracker.close()
        self._inbound.end()

    def _fire_mark(self, tag: str) -> None:
        self.log.append(("mark_fired", (tag,)))
        for cb in self._mark_cbs:
            cb(tag)
