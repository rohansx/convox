"""Call clock. All timers (EOU, barge-in, 4 s / 10 s ticks) read this, never wall time,
so tests can run faster than real time with FakeClock."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from typing import Protocol


class Clock(Protocol):
    def now_ms(self) -> int: ...


class MonotonicClock:
    """Milliseconds since construction (i.e. call-relative when made at call start)."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()

    def now_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    async def sleep_ms(self, ms: int) -> None:
        await asyncio.sleep(max(0, ms) / 1000)


class FakeClock:
    """Time moves only on advance()/set(). sleep_ms() waits for clock time, not wall time,
    so fake providers can model vendor latency deterministically."""

    def __init__(self, start_ms: int = 0) -> None:
        self._now = start_ms
        self._sleepers: list[tuple[int, int, asyncio.Future[None]]] = []
        self._order = itertools.count()

    def now_ms(self) -> int:
        return self._now

    def advance(self, ms: int) -> None:
        assert ms >= 0
        self._now += ms
        self._wake()

    def set(self, ms: int) -> None:
        assert ms >= self._now
        self._now = ms
        self._wake()

    async def sleep_ms(self, ms: int) -> None:
        if ms <= 0:
            await asyncio.sleep(0)
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (self._now + ms, next(self._order), fut))
        await fut

    def _wake(self) -> None:
        while self._sleepers and self._sleepers[0][0] <= self._now:
            _, _, fut = heapq.heappop(self._sleepers)
            if not fut.done():
                fut.set_result(None)
