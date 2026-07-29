"""Trial artifacts: everything produced by a single simulated call.

A trial bundle is the contract between the simulation engine and everything
downstream — evaluation, reporting, the dashboard, and third-party tooling. It is
serialisable, self-describing, and sufficient to re-score a call without placing
it again.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from convox.model.enums import (
    AssertionStatus,
    EndedBy,
    Layer,
    Speaker,
    TrialStatus,
    Verdict,
)

#: Bumped when metric or assertion semantics change, so old runs stay interpretable.
ARTIFACT_VERSION = 1


class Event(BaseModel):
    """A timestamped occurrence on the call, in milliseconds from connect."""

    model_config = ConfigDict(extra="forbid")

    at_ms: int
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    at_ms: int
    result: Any = None


class Turn(BaseModel):
    """One party's contribution to the conversation.

    ``ground_truth_text`` is populated for caller turns only, and is the exact text
    Convox emitted — known before any audio existed. Every ASR and slot measurement
    is a comparison against this field, which is what makes those measurements exact
    rather than inferred.
    """

    model_config = ConfigDict(extra="forbid")

    index: int
    speaker: Speaker
    ground_truth_text: str | None = None
    heard_text: str | None = None
    convox_transcript: str | None = None

    speech_start_ms: int
    speech_end_ms: int
    first_audio_ms: int | None = None

    interrupted: bool = False
    interrupted_at_ms: int | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)

    @property
    def text(self) -> str:
        """Best available text for this turn."""
        return self.ground_truth_text or self.heard_text or self.convox_transcript or ""


class AudioAnalysis(BaseModel):
    """Measured properties of one leg's audio."""

    model_config = ConfigDict(extra="forbid")

    duration_ms: int = 0
    rms: float = 0.0
    peak: float = 0.0
    snr_db: float | None = None
    clipping_ratio: float = 0.0
    silence_ratio: float = 1.0
    artifact_score: float = 0.0
    truncated: bool = False
    speech_ms: int = 0


class BargeInEvent(BaseModel):
    """A measured interruption: when the caller cut in, and how the agent reacted."""

    model_config = ConfigDict(extra="forbid")

    caller_start_ms: int
    agent_stopped_ms: int | None = None
    stop_ms: int | None = None
    overrun_ms: int = 0
    overrun_bytes: int = 0
    addressed: bool = False


class AudioTrack(BaseModel):
    """Audio-side record of the call.

    Analysis is computed once, at the end of the call, and carried in the bundle
    so every downstream consumer reads the same numbers. Raw WAVs are written
    alongside when an artifact directory is configured.
    """

    model_config = ConfigDict(extra="forbid")

    sample_rate: int
    agent: AudioAnalysis | None = None
    caller: AudioAnalysis | None = None
    agent_wav: str | None = None
    caller_wav: str | None = None
    barge_ins: list[BargeInEvent] = Field(default_factory=list)


class MetricValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: float | None = None
    unit: str | None = None
    #: False when the target cannot supply the inputs this metric needs.
    available: bool = True
    detail: dict[str, Any] = Field(default_factory=dict)


class AssertionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: AssertionStatus
    soft: bool = False
    expected: Any = None
    actual: Any = None
    message: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    suspected_layer: Layer | None = None

    @property
    def counts_as_failure(self) -> bool:
        return self.status in (AssertionStatus.FAIL, AssertionStatus.ERROR) and not self.soft


class CostEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: str  # caller_stt | caller_llm | caller_tts | judge | telephony
    usd: float = 0.0
    detail: dict[str, Any] = Field(default_factory=dict)


class TrialArtifacts(BaseModel):
    """The raw record of one call, before evaluation."""

    model_config = ConfigDict(extra="forbid")

    artifact_version: int = ARTIFACT_VERSION
    trial_id: str
    run_id: str | None = None
    scenario_name: str
    scenario_hash: str
    repeat_index: int = 0
    seed: int = 0

    persona_name: str | None = None
    language: str | None = None
    target_name: str | None = None
    target_kind: str | None = None
    capabilities: dict[str, bool] = Field(default_factory=dict)

    status: TrialStatus = TrialStatus.COMPLETED
    ended_by: EndedBy | None = None
    error: str | None = None

    started_at: str | None = None
    duration_ms: int = 0

    turns: list[Turn] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    costs: list[CostEntry] = Field(default_factory=list)
    audio: AudioTrack | None = None

    # ── convenience accessors used throughout evaluation ──

    def turns_by(self, speaker: Speaker) -> list[Turn]:
        return [t for t in self.turns if t.speaker == speaker]

    @property
    def agent_turns(self) -> list[Turn]:
        return self.turns_by(Speaker.AGENT)

    @property
    def caller_turns(self) -> list[Turn]:
        return self.turns_by(Speaker.CALLER)

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [tc for t in self.turns for tc in t.tool_calls]

    @property
    def cost_usd(self) -> float:
        return round(sum(c.usd for c in self.costs), 6)

    def transcript_text(self, speaker: Speaker | None = None) -> str:
        turns = self.turns if speaker is None else self.turns_by(speaker)
        return "\n".join(t.text for t in turns if t.text)

    def supports(self, capability: str) -> bool:
        return bool(self.capabilities.get(capability, False))


class TrialResult(BaseModel):
    """A trial after evaluation."""

    model_config = ConfigDict(extra="forbid")

    artifacts: TrialArtifacts
    metrics: list[MetricValue] = Field(default_factory=list)
    assertions: list[AssertionResult] = Field(default_factory=list)
    verdict: Verdict = Verdict.PASS
    suspected_layer: Layer | None = None

    def metric(self, name: str) -> MetricValue | None:
        return next((m for m in self.metrics if m.name == name), None)

    @property
    def failures(self) -> list[AssertionResult]:
        return [a for a in self.assertions if a.counts_as_failure]

    @property
    def unsupported(self) -> list[AssertionResult]:
        return [a for a in self.assertions if a.status == AssertionStatus.UNSUPPORTED]


class ScenarioOutcome(BaseModel):
    """Aggregate of every repeat of one scenario — the ``pass^k`` unit."""

    model_config = ConfigDict(extra="forbid")

    scenario_name: str
    trials: list[TrialResult] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.trials)

    @property
    def passed(self) -> int:
        return sum(1 for t in self.trials if t.verdict == Verdict.PASS)

    @property
    def pass_k(self) -> float:
        """Fraction of repeats that passed. A scenario at 3/5 is a 60% agent."""
        return self.passed / self.total if self.total else 0.0

    @property
    def verdict(self) -> Verdict:
        if any(t.verdict == Verdict.ERROR for t in self.trials):
            return Verdict.ERROR
        if self.passed == self.total:
            return Verdict.PASS
        if self.passed == 0:
            return Verdict.FAIL
        return Verdict.FLAKY


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    target_name: str
    started_at: str
    outcomes: list[ScenarioOutcome] = Field(default_factory=list)

    @property
    def trials(self) -> list[TrialResult]:
        return [t for o in self.outcomes for t in o.trials]

    @property
    def trials_passed(self) -> int:
        return sum(1 for t in self.trials if t.verdict == Verdict.PASS)

    @property
    def scenarios_passed(self) -> int:
        return sum(1 for o in self.outcomes if o.verdict == Verdict.PASS)

    @property
    def cost_usd(self) -> float:
        return round(sum(t.artifacts.cost_usd for t in self.trials), 6)

    @property
    def ok(self) -> bool:
        return all(o.verdict == Verdict.PASS for o in self.outcomes)
