"""Target adapter interface.

Adapters are the only part of Convox that knows what platform an agent runs on.
They expose exactly one contract upward: *give me a conversation channel, plus
whatever side-channel facts this platform happens to expose*. Personas, scenarios,
assertions, and metrics sit above this line and are platform-agnostic by
construction — which is what makes staying neutral across Retell, Vapi, LiveKit,
Pipecat, and raw sockets cheap rather than a maintenance treadmill.

An adapter must declare its capabilities honestly. Claiming `tool_calls` and never
emitting one is a contract-test failure, not a quirk: assertions that cannot be
measured must surface as `unsupported`, never as a silent pass.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from convox.model.target import AdapterCapabilities, Target


class Clock:
    """Monotonic call clock. All timestamps are milliseconds from connect.

    Carries an optional virtual offset so that a run can model the passage of
    speaking time without actually waiting for it. Both legs of the conversation
    read this one clock, which is the point: if the caller's timeline advanced
    while agent events were stamped from the wall clock, the two would drift apart
    and every turn would be attributed to the wrong moment.
    """

    def __init__(self) -> None:
        self._t0_ns: int | None = None
        self._virtual_offset_ms: int = 0

    def start(self) -> None:
        self._t0_ns = time.monotonic_ns()

    @property
    def started(self) -> bool:
        return self._t0_ns is not None

    def advance(self, ms: int) -> None:
        """Move the clock forward without sleeping."""
        if ms > 0:
            self._virtual_offset_ms += ms

    def now_ms(self) -> int:
        if self._t0_ns is None:
            return 0
        elapsed = (time.monotonic_ns() - self._t0_ns) // 1_000_000
        return elapsed + self._virtual_offset_ms


@dataclass
class TrialContext:
    """Everything an adapter needs to place one call."""

    trial_id: str
    scenario_name: str
    clock: Clock
    facts: dict[str, Any] = field(default_factory=dict)
    dynamic_variables: dict[str, Any] = field(default_factory=dict)
    timeout_s: int = 300


@dataclass
class AgentEvent:
    """Something the agent did, stamped on the call clock.

    Kinds:
        agent.speech_start   the agent began producing output
        agent.text           an utterance (``final`` marks the end of a turn)
        agent.speech_end     the agent stopped producing output
        agent.tool_call      a tool/function invocation
        agent.hangup         the agent ended the call
        error                transport or platform error
    """

    kind: str
    at_ms: int
    payload: dict[str, Any] = field(default_factory=dict)


class UnsupportedCapability(RuntimeError):
    """Raised when a scenario asks an adapter to do something it cannot."""


class AdapterError(RuntimeError):
    """Transport or platform failure. Distinct from an agent behaving badly."""


class TargetSession(ABC):
    """One live conversation with the agent under test."""

    def __init__(self, ctx: TrialContext, capabilities: AdapterCapabilities) -> None:
        self.ctx = ctx
        self.capabilities = capabilities

    @abstractmethod
    async def say(self, text: str, *, interrupting: bool = False) -> None:
        """Deliver a caller utterance. ``text`` is recorded as ground truth."""

    @abstractmethod
    def events(self) -> AsyncIterator[AgentEvent]:
        """Stream agent-side events until the call ends."""

    async def send_dtmf(self, digits: str, *, tone_ms: int = 120, gap_ms: int = 80) -> None:
        raise UnsupportedCapability("dtmf")

    @abstractmethod
    async def hangup(self) -> None:
        """Caller-initiated disconnect."""

    @abstractmethod
    async def close(self) -> None:
        """Release transport resources. Must be safe to call twice."""


class TargetAdapter(ABC):
    """Connects to one kind of agent platform."""

    kind: str = "base"
    capabilities: AdapterCapabilities = AdapterCapabilities()

    def __init__(self, target: Target) -> None:
        self.target = target
        self.config = target.config

    @abstractmethod
    async def connect(self, ctx: TrialContext) -> TargetSession:
        """Open a call. Starts ``ctx.clock`` at the moment the call connects."""

    async def snapshot_config(self) -> dict[str, Any] | None:
        """Capture the agent's prompt/version, where the platform exposes it.

        Recorded with each run so a regression can be diffed against the
        configuration change that caused it.
        """
        return None

    async def health_check(self, timeout_s: float = 10.0) -> tuple[bool, str]:
        """Verify the target is reachable before a suite is run against it."""
        ctx = TrialContext(
            trial_id="healthcheck",
            scenario_name="healthcheck",
            clock=Clock(),
            timeout_s=int(timeout_s),
        )
        try:
            session = await self.connect(ctx)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            return False, f"{type(exc).__name__}: {exc}"
        try:
            await session.hangup()
            return True, "reachable"
        finally:
            await session.close()
