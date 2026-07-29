"""Scripted caller policy — an exact, ordered list of utterances.

Fully deterministic: the same words in the same order every run, no model in the
caller loop, and any failure is unambiguously the agent's. This is the policy
regression suites should use; agentic callers buy coverage at the cost of variance.

Turn discipline: the caller waits for the agent to finish before speaking again.
``barge_in`` is the explicit exception, and ``wait_silence`` deliberately declines
to speak so that silence-timeout handling can be probed.
"""

from __future__ import annotations

import asyncio

from convox.adapters.base import TargetSession
from convox.model.enums import EndedBy
from convox.model.persona import Persona
from convox.model.scenario import (
    Backchannel,
    BargeIn,
    Dtmf,
    Hangup,
    Say,
    SayFact,
    Scenario,
    ScriptStep,
    WaitForAgent,
    WaitSilence,
)
from convox.sim.conversation import AgentStream
from convox.sim.recorder import Recorder, estimate_speech_ms

DEFAULT_AGENT_TIMEOUT_MS = 15_000

#: How long to wait for an agent that greets on connect before the caller speaks.
#: Short, because an agent that never greets should not cost the whole suite.
GREETING_TIMEOUT_MS = 5_000


def speakable(value: object) -> str:
    """Render a fact for speech.

    Digit strings are spaced so that a TTS voice reads them as digits rather than
    as a number, which is how a real caller would say a phone number.
    """
    text = str(value)
    digits = [c for c in text if c.isdigit()]
    if digits and len(digits) >= 6 and all(c.isdigit() or c in "+- ()" for c in text):
        return " ".join(digits)
    return text


class ScriptedPolicy:
    def __init__(
        self,
        scenario: Scenario,
        persona: Persona,
        recorder: Recorder,
        stream: AgentStream,
        *,
        realtime: bool = True,
    ) -> None:
        self.scenario = scenario
        self.persona = persona
        self.recorder = recorder
        self.stream = stream
        self.realtime = realtime
        self.ended_by: EndedBy | None = None
        #: On an inbound call the agent speaks first, so the caller starts by
        #: listening rather than talking over the greeting.
        self._awaiting_agent = True
        self._agent_timeout_ms = GREETING_TIMEOUT_MS
        self._last_caller_end_ms = 0
        self._turns_spoken = 0

    # ── helpers ──

    def _steps(self) -> list[ScriptStep]:
        steps: list[ScriptStep] = []
        if self.scenario.caller.opening:
            steps.append(Say(text=self.scenario.caller.opening))
        steps.extend(self.scenario.caller.script)
        return steps

    async def _sleep_ms(self, ms: int) -> None:
        """Let time pass — really, or virtually when pacing is disabled.

        Either way the call clock moves, so caller and agent timestamps stay on
        one timeline and measurements remain comparable between modes.
        """
        if ms <= 0:
            return
        if self.realtime:
            await asyncio.sleep(ms / 1000)
        else:
            self.recorder.clock.advance(ms)

    async def _speak(self, session: TargetSession, text: str, *, interrupting: bool = False) -> None:
        """Utter one caller turn, holding the floor for its estimated duration."""
        start_ms = self.recorder.clock.now_ms()
        duration = estimate_speech_ms(text, self.persona.speech_rate)
        await self._sleep_ms(duration)
        end_ms = self.recorder.clock.now_ms()
        self.recorder.caller_turn(text, start_ms=start_ms, end_ms=end_ms, interrupting=interrupting)
        await session.say(text, interrupting=interrupting)
        self._last_caller_end_ms = end_ms
        self._awaiting_agent = True
        self._agent_timeout_ms = DEFAULT_AGENT_TIMEOUT_MS
        self._turns_spoken += 1

    async def _yield_to_agent(self, timeout_ms: int | None = None) -> None:
        """Let the agent finish before the caller speaks again."""
        if not self._awaiting_agent:
            return
        self._awaiting_agent = False
        await self.stream.wait_for_turn(
            timeout_ms or self._agent_timeout_ms,
            after_ms=self._last_caller_end_ms,
        )

    def _fact(self, field: str) -> str:
        facts = self.scenario.caller.facts
        if field not in facts:
            raise KeyError(f"scenario {self.scenario.name!r} has no fact {field!r}")
        return speakable(facts[field])

    # ── execution ──

    async def run(self, session: TargetSession) -> EndedBy:
        """Execute the script. Returns who ended the call."""
        for step in self._steps():
            if self.stream.agent_hung_up:
                self.ended_by = EndedBy.AGENT
                break
            if self._turns_spoken >= self.scenario.max_turns:
                self.recorder.event("caller.max_turns_reached", turns=self._turns_spoken)
                break

            match step:
                case Say():
                    await self._yield_to_agent()
                    await self._speak(session, step.text)

                case SayFact():
                    await self._yield_to_agent()
                    await self._speak(session, self._fact(step.field))

                case WaitForAgent():
                    self._awaiting_agent = True
                    await self._yield_to_agent(step.timeout_ms)

                case WaitSilence():
                    # Marked so dead-air metrics can exclude silence we chose.
                    self.recorder.event("caller.wait_silence", ms=step.ms, deliberate=True)
                    await self._sleep_ms(step.ms)

                case BargeIn():
                    await self._sleep_ms(step.after_ms)
                    self._awaiting_agent = False
                    self.recorder.event("caller.barge_in", after_ms=step.after_ms)
                    await self._speak(session, step.say, interrupting=True)

                case Backchannel():
                    await self._sleep_ms(step.after_ms)
                    self.recorder.event("caller.backchannel", text=step.text)
                    await session.say(step.text, interrupting=True)

                case Dtmf():
                    await self._yield_to_agent()
                    await session.send_dtmf(step.digits, tone_ms=step.tone_ms, gap_ms=step.gap_ms)
                    self.recorder.event("caller.dtmf", digits=step.digits)
                    self._last_caller_end_ms = self.recorder.clock.now_ms()
                    self._awaiting_agent = True
                    self._agent_timeout_ms = DEFAULT_AGENT_TIMEOUT_MS

                case Hangup():
                    await session.hangup()
                    self.ended_by = EndedBy.CALLER
                    return self.ended_by

        # Give the agent a chance to finish its last turn before we decide.
        await self._yield_to_agent()

        if self.ended_by is None:
            self.ended_by = EndedBy.AGENT if self.stream.agent_hung_up else EndedBy.CALLER
        if self.ended_by == EndedBy.CALLER:
            await session.hangup()
        return self.ended_by
