"""FakeSession and FakeSessionRegistry: test doubles for the shared CallSession /
SessionRegistry contract, so API tests never depend on the real session."""

from __future__ import annotations

import asyncio
from typing import Any

from convox.config.pairs import PairConfig
from convox.events import EventBus
from convox.types import CallEvent, Side, Turn


class FakeSession:
    def __init__(
        self,
        call_id: str,
        pair: PairConfig,
        providers: Any = None,
        *,
        bus: EventBus,
        mode: str = "interpret",
        escape_dtmf: str = "0",
        record: bool = False,
        **kw: Any,
    ) -> None:
        self.call_id = call_id
        self.pair = pair
        self.bus = bus
        self.mode = mode
        self.escape_dtmf = escape_dtmf
        self.record = record
        self.kw = kw
        self.status = "ringing"
        self.legs: dict[Side, Any] = {}
        self.hangups: list[str] = []
        self.turn_log: list[Turn] = []
        self._closed = asyncio.Event()

    async def attach_leg(self, side: Side, leg: Any) -> None:
        if side in self.legs:
            raise ValueError(f"side {side} already attached")
        self.legs[side] = leg
        if len(self.legs) == 2 and self.status == "ringing":
            self.status = "active"
            self.bus.publish(CallEvent(self.call_id, 0, "call", {"status": "active"}))

    async def hangup(self, reason: str = "hangup") -> None:
        self.hangups.append(reason)
        if self.status == "ended":
            return
        self.status = "ended"
        for leg in self.legs.values():
            await leg.close()
        self.bus.publish(CallEvent(self.call_id, 0, "call", {"status": "ended"}))
        self._closed.set()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    def add_turn(self, turn: Turn) -> None:
        self.turn_log.append(turn)
        self.bus.publish(CallEvent(self.call_id, 0, "turn", {"turn": turn}))

    def summary(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "status": self.status,
            "mode": self.mode,
            "pair": self.pair.key,
            "turns": len([t for t in self.turn_log if not t.backchannel]),
            "p50_first_audio_ms": 1500 if self.turn_log else None,
            "p95_first_audio_ms": 1820 if self.turn_log else None,
            "escape_used": False,
            "duration_s": 15 if self.status == "ended" else None,
        }


class FakeSessionRegistry:
    def __init__(self) -> None:
        self._s: dict[str, Any] = {}

    def create(self, call_id: str, session: Any) -> None:
        if call_id in self._s:
            raise KeyError(call_id)
        self._s[call_id] = session

    def get(self, call_id: str) -> Any | None:
        return self._s.get(call_id)

    def remove(self, call_id: str) -> None:
        self._s.pop(call_id, None)

    def list(self) -> list[Any]:
        return list(self._s.values())


def install(app: Any, *, registry: FakeSessionRegistry | None = None) -> FakeSessionRegistry:
    """Override the session dependencies on `app` with the doubles."""
    from convox.api import deps

    reg = registry or FakeSessionRegistry()
    created: list[FakeSession] = []

    def factory_dep() -> Any:
        def make(call_id: str, pair: PairConfig, **kw: Any) -> FakeSession:
            s = FakeSession(call_id, pair, bus=app.state.bus, **kw)
            created.append(s)
            return s

        return make

    app.dependency_overrides[deps.get_session_factory] = factory_dep
    app.dependency_overrides[deps.get_session_registry] = lambda: reg
    app.state.fake_sessions = created
    return reg


async def until(pred: Any, within_s: float = 1.0) -> None:
    """Poll `pred` on the event loop until true (background tasks need a few turns)."""
    for _ in range(int(within_s / 0.005)):
        if pred():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met")
