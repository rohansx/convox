"""Scores a trial: metrics first, then assertions, then failure attribution.

Evaluation is deliberately separate from simulation and is idempotent — a trial
bundle can be re-scored any number of times with better assertions or a better
judge, without placing the call again.
"""

from __future__ import annotations

from convox.eval import metrics as metrics_mod
from convox.eval import registry
from convox.eval.assertions import load_builtins
from convox.model.enums import AssertionStatus, Layer, TrialStatus, Verdict
from convox.model.scenario import Scenario
from convox.model.trial import (
    AssertionResult,
    ScenarioOutcome,
    TrialArtifacts,
    TrialResult,
)

load_builtins()


def attribute(results: list[AssertionResult]) -> Layer | None:
    """Pick the layer most likely responsible for a trial's failures.

    Ordered by how far upstream the fault is: a recognition error explains a
    downstream reasoning mistake, so if both are implicated, blame recognition.
    """
    implicated = [r.suspected_layer for r in results if r.counts_as_failure and r.suspected_layer]
    if not implicated:
        return None
    for layer in (Layer.STT, Layer.TTS, Layer.TIMING, Layer.TOOL, Layer.LLM):
        if layer in implicated:
            return layer
    return implicated[0]


def evaluate_trial(artifacts: TrialArtifacts, scenario: Scenario) -> TrialResult:
    computed = metrics_mod.compute(artifacts)

    # An infrastructure failure is not the agent's fault and must not be reported
    # as one — assertions are meaningless against a call that never happened.
    if artifacts.status == TrialStatus.INFRASTRUCTURE_ERROR:
        return TrialResult(
            artifacts=artifacts,
            metrics=computed,
            assertions=[],
            verdict=Verdict.ERROR,
        )

    results = [registry.run(artifacts, spec) for spec in scenario.assertions]
    hard_failures = [r for r in results if r.counts_as_failure]
    verdict = Verdict.FAIL if hard_failures else Verdict.PASS

    return TrialResult(
        artifacts=artifacts,
        metrics=computed,
        assertions=results,
        verdict=verdict,
        suspected_layer=attribute(results),
    )


def summarize(scenario_name: str, trials: list[TrialResult]) -> ScenarioOutcome:
    return ScenarioOutcome(scenario_name=scenario_name, trials=trials)


def unsupported_summary(results: list[TrialResult]) -> dict[str, str]:
    """Assertions that could not be measured, and why — surfaced in every report.

    A run that quietly skips half its checks looks identical to one that passed
    them. Reporting the gap is the difference between a tool and a placebo.
    """
    reasons: dict[str, str] = {}
    for trial in results:
        for result in trial.assertions:
            if result.status == AssertionStatus.UNSUPPORTED:
                reasons.setdefault(result.name, result.message or "unsupported")
    return reasons
