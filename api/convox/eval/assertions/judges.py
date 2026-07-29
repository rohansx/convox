"""Judged assertions — semantic claims that need a model.

Judges are treated as instruments that need calibration, not oracles. Three rules
are baked into the contract before any backend exists, because retrofitting them
later is how eval tools end up with numbers nobody trusts:

1. A judge runs only after the cheap deterministic checks pass, so a trial can
   never report "the judge said the goal was met" while the required tool call is
   missing.
2. A verdict must cite the turn ids that justify it; an uncited verdict is
   rejected and retried rather than believed.
3. Judges vote. Disagreement is surfaced as confidence, not averaged away.

Until a judge backend is configured, these report ``unsupported`` — never a pass.
Silently passing an unrun semantic check is the failure mode this whole design
exists to avoid.
"""

from __future__ import annotations

from convox.eval.registry import assertion, unsupported
from convox.model.scenario import AssertionSpec
from convox.model.trial import TrialArtifacts

NO_BACKEND = "no judge backend configured (set `judge.llm` in convox.yaml)"


@assertion("judge")
def judge_rubric(artifacts: TrialArtifacts, spec: AssertionSpec):
    return unsupported(spec, NO_BACKEND)


@assertion("judge.goal_achieved")
def judge_goal_achieved(artifacts: TrialArtifacts, spec: AssertionSpec):
    return unsupported(spec, NO_BACKEND)


@assertion("judge.instruction_following")
def judge_instruction_following(artifacts: TrialArtifacts, spec: AssertionSpec):
    return unsupported(spec, NO_BACKEND)


@assertion("judge.tone")
def judge_tone(artifacts: TrialArtifacts, spec: AssertionSpec):
    return unsupported(spec, NO_BACKEND)


@assertion("judge.no_hallucination")
def judge_no_hallucination(artifacts: TrialArtifacts, spec: AssertionSpec):
    return unsupported(spec, NO_BACKEND)
