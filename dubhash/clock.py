"""Call clock. All timers (EOU, barge-in, 4 s / 10 s ticks) read this, never wall time,
so tests can run faster than real time with FakeClock."""

from __future__ import annotations

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


class FakeClock:
    def __init__(self, start_ms: int = 0) -> None:
        self._now = start_ms

    def now_ms(self) -> int:
        return self._now

    def advance(self, ms: int) -> None:
        assert ms >= 0
        self._now += ms

    def set(self, ms: int) -> None:
        assert ms >= self._now
        self._now = ms
