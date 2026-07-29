"""Records everything that happens on a call into a trial artifact bundle.

The recorder is the single writer of ground truth. Caller utterances are logged
here at the moment they are *emitted* — before any synthesis — which is what makes
downstream ASR and slot measurements exact comparisons rather than estimates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from convox.adapters.base import Clock
from convox.model.enums import EndedBy, Speaker, TrialStatus
from convox.model.trial import CostEntry, Event, ToolCall, TrialArtifacts, Turn

#: Baseline speaking pace used to estimate utterance duration on text-only
#: channels, where no audio exists to measure. Metrics derived from an estimate
#: carry ``detail["estimated"] = True`` so nothing silently passes as measured.
WORDS_PER_MINUTE = 150.0


def estimate_speech_ms(text: str, rate: float = 1.0) -> int:
    words = max(1, len(text.split()))
    return int(words / (WORDS_PER_MINUTE * max(rate, 0.1)) * 60_000)


class Recorder:
    def __init__(
        self,
        *,
        clock: Clock,
        trial_id: str,
        run_id: str | None = None,
        scenario_name: str,
        scenario_hash: str,
        repeat_index: int = 0,
        seed: int = 0,
        persona_name: str | None = None,
        language: str | None = None,
        target_name: str | None = None,
        target_kind: str | None = None,
        capabilities: dict[str, bool] | None = None,
        facts: dict[str, Any] | None = None,
    ) -> None:
        self.clock = clock
        self.started_at = datetime.now(UTC).isoformat()
        self.trial_id = trial_id
        self._meta = {
            "trial_id": trial_id,
            "run_id": run_id,
            "scenario_name": scenario_name,
            "scenario_hash": scenario_hash,
            "repeat_index": repeat_index,
            "seed": seed,
            "persona_name": persona_name,
            "language": language,
            "target_name": target_name,
            "target_kind": target_kind,
            "capabilities": capabilities or {},
            "facts": facts or {},
        }
        self.events: list[Event] = []
        self.turns: list[Turn] = []
        self.costs: list[CostEntry] = []
        self._open_agent_turn: Turn | None = None

    # ── timeline ──

    def event(self, kind: str, at_ms: int | None = None, **payload: Any) -> Event:
        evt = Event(at_ms=self.clock.now_ms() if at_ms is None else at_ms, kind=kind, payload=payload)
        self.events.append(evt)
        return evt

    def cost(self, component: str, usd: float, **detail: Any) -> None:
        self.costs.append(CostEntry(component=component, usd=usd, detail=detail))

    # ── caller ──

    def caller_turn(
        self,
        text: str,
        *,
        start_ms: int,
        end_ms: int,
        interrupting: bool = False,
    ) -> Turn:
        """Record a caller utterance. ``text`` is the ground truth."""
        turn = Turn(
            index=len(self.turns),
            speaker=Speaker.CALLER,
            ground_truth_text=text,
            speech_start_ms=start_ms,
            speech_end_ms=end_ms,
        )
        self.turns.append(turn)
        self.event(
            "caller.speech",
            at_ms=start_ms,
            text=text,
            end_ms=end_ms,
            interrupting=interrupting,
            turn=turn.index,
        )
        return turn

    # ── agent ──

    def agent_speech_start(self, at_ms: int) -> Turn:
        if self._open_agent_turn is not None:
            return self._open_agent_turn
        turn = Turn(
            index=len(self.turns),
            speaker=Speaker.AGENT,
            speech_start_ms=at_ms,
            speech_end_ms=at_ms,
            first_audio_ms=at_ms,
        )
        self.turns.append(turn)
        self._open_agent_turn = turn
        self.event("agent.speech_start", at_ms=at_ms, turn=turn.index)
        return turn

    def agent_text(self, text: str, at_ms: int) -> Turn:
        turn = self.agent_speech_start(at_ms)
        if turn.first_audio_ms is None:
            turn.first_audio_ms = at_ms
        turn.heard_text = f"{turn.heard_text} {text}".strip() if turn.heard_text else text
        self.event("agent.text", at_ms=at_ms, text=text, turn=turn.index)
        return turn

    def agent_tool_call(self, name: str, args: dict[str, Any], at_ms: int) -> None:
        turn = self.agent_speech_start(at_ms)
        turn.tool_calls.append(ToolCall(name=name, args=args, at_ms=at_ms))
        self.event("agent.tool_call", at_ms=at_ms, name=name, args=args, turn=turn.index)

    def agent_speech_end(self, at_ms: int | None = None, *, estimated: bool = False) -> Turn | None:
        turn = self._open_agent_turn
        if turn is None:
            return None
        if at_ms is None:
            at_ms = turn.speech_start_ms + estimate_speech_ms(turn.heard_text or "")
            estimated = True
        turn.speech_end_ms = max(at_ms, turn.speech_start_ms)
        self._open_agent_turn = None
        self.event("agent.speech_end", at_ms=turn.speech_end_ms, turn=turn.index, estimated=estimated)
        return turn

    @property
    def agent_turn_open(self) -> bool:
        return self._open_agent_turn is not None

    # ── finish ──

    def finalize(
        self,
        *,
        status: TrialStatus = TrialStatus.COMPLETED,
        ended_by: EndedBy | None = None,
        error: str | None = None,
    ) -> TrialArtifacts:
        self.agent_speech_end()
        duration = self.clock.now_ms()
        self.event("call.ended", at_ms=duration, ended_by=ended_by.value if ended_by else None)
        # Sorted chronologically for reading; `index` stays as assigned so that
        # evidence recorded against a turn id remains valid.
        self.turns.sort(key=lambda t: (t.speech_start_ms, t.index))
        return TrialArtifacts(
            **self._meta,
            status=status,
            ended_by=ended_by,
            error=error,
            started_at=self.started_at,
            duration_ms=duration,
            turns=self.turns,
            events=sorted(self.events, key=lambda e: e.at_ms),
            costs=self.costs,
        )
