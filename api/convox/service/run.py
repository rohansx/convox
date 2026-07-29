"""Run planning and execution: scenarios × repeats → trials → results.

Repeats are the point. A voice test that passes once proves very little, so the
unit of judgement is ``pass^k`` across k independent trials rather than a single
verdict — a scenario that passes 3 times out of 5 is a 60% agent, and reporting
it as green would be a lie the tool tells its user.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from convox.eval.evaluator import evaluate_trial
from convox.model.persona import DEFAULT_PERSONA, Persona
from convox.model.scenario import Scenario
from convox.model.target import Target
from convox.model.trial import RunResult, ScenarioOutcome, TrialResult
from convox.sim.runner import RunOptions, run_trial


@dataclass
class PlannedTrial:
    scenario: Scenario
    persona: Persona
    repeat_index: int


@dataclass
class RunSpec:
    target: Target
    scenarios: list[Scenario]
    personas: dict[str, Persona] = field(default_factory=dict)
    repeat_override: int | None = None
    options: RunOptions = field(default_factory=RunOptions)
    #: Trials in flight at once. Bounded per-target to avoid tripping rate limits.
    concurrency: int = 4

    def persona_for(self, scenario: Scenario) -> Persona:
        if isinstance(scenario.persona, Persona):
            return scenario.persona
        if isinstance(scenario.persona, str):
            persona = self.personas.get(scenario.persona)
            if persona is None:
                raise KeyError(
                    f"scenario {scenario.name!r} references unknown persona {scenario.persona!r}"
                )
            return persona
        return DEFAULT_PERSONA


def plan(spec: RunSpec) -> list[PlannedTrial]:
    planned: list[PlannedTrial] = []
    for scenario in spec.scenarios:
        repeats = spec.repeat_override or scenario.repeat
        persona = spec.persona_for(scenario)
        planned.extend(PlannedTrial(scenario, persona, i) for i in range(repeats))
    return planned


ProgressFn = Callable[[TrialResult], None]


async def execute(spec: RunSpec, on_trial: ProgressFn | None = None) -> RunResult:
    run_id = spec.options.run_id or uuid.uuid4().hex[:8]
    spec.options.run_id = run_id
    started_at = datetime.now(UTC).isoformat()

    planned = plan(spec)
    semaphore = asyncio.Semaphore(max(1, spec.concurrency))
    results: dict[str, list[TrialResult]] = {s.name: [] for s in spec.scenarios}
    lock = asyncio.Lock()

    async def one(item: PlannedTrial) -> None:
        async with semaphore:
            artifacts = await run_trial(
                item.scenario,
                spec.target,
                persona=item.persona,
                repeat_index=item.repeat_index,
                options=spec.options,
            )
        result = evaluate_trial(artifacts, item.scenario)
        async with lock:
            results[item.scenario.name].append(result)
            if on_trial is not None:
                on_trial(result)

    await asyncio.gather(*(one(item) for item in planned))

    outcomes = [
        ScenarioOutcome(
            scenario_name=scenario.name,
            trials=sorted(results[scenario.name], key=lambda t: t.artifacts.repeat_index),
        )
        for scenario in spec.scenarios
    ]
    return RunResult(
        run_id=run_id,
        target_name=spec.target.name,
        started_at=started_at,
        outcomes=outcomes,
    )
