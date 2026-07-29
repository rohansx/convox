"""Convox tested against an agent whose bugs we already know.

A testing tool whose own reliability is unproven is worthless. These tests run the
full pipeline against the reference agent with specific faults switched on, and
assert that Convox reports *exactly* those faults — no false negatives, and just
as importantly no false positives, since a suite that cries wolf gets ignored.
"""

import pytest

from convox.model.enums import Verdict
from convox.service.run import RunSpec, execute
from convox.testing.reference_agent import Bugs, running_agent
from tests.conftest import refill_scenario, ws_target

#: The standing checks every run of the reference agent should satisfy.
STANDARD_ASSERTIONS = [
    {"tool.called": "create_refill_order"},
    {"slot.captured": {"field": "phone", "value": "+919876543210", "normalize": "e164"}},
    {"transcript.no_repetition": {"max_repeats": 2}},
    {"latency.response_ms": {"max": {"lt": 600}}},
    {"call.no_error_frames": True},
]


async def run_suite(bugs, options, repeat=1, assertions=None):
    scenario = refill_scenario(**{"assert": assertions or STANDARD_ASSERTIONS, "repeat": repeat})
    async with running_agent(bugs) as url:
        return await execute(RunSpec(target=ws_target(url), scenarios=[scenario], options=options))


def failed_assertions(result) -> set[str]:
    return {f.name for trial in result.trials for f in trial.failures}


async def test_healthy_agent_produces_no_findings(fast_options):
    """The false-positive check. If this ever fails, nothing else here matters."""
    result = await run_suite(Bugs(), fast_options)
    assert result.ok, f"clean agent reported failures: {failed_assertions(result)}"


@pytest.mark.parametrize(
    ("bugs", "expected"),
    [
        (Bugs(skip_tool_call=True), {"tool.called"}),
        (Bugs(mangle_digits=True), {"slot.captured"}),
        (Bugs(repeat_loop=True), {"transcript.no_repetition", "tool.called", "slot.captured"}),
        (Bugs(slow_responses_ms=900), {"latency.response_ms"}),
    ],
    ids=["missing_tool_call", "wrong_digits", "repetition_loop", "slow_responses"],
)
async def test_injected_bugs_are_caught_exactly(bugs, expected, fast_options):
    result = await run_suite(bugs, fast_options)

    assert not result.ok, "an agent with an injected bug must not pass"
    assert failed_assertions(result) == expected


async def test_verdicts_are_stable_across_repeats(fast_options):
    """Determinism check: a scripted caller against a fixed agent must not flake.

    If our own harness produces different verdicts run to run, every `pass^k`
    number it reports is noise.
    """
    result = await run_suite(Bugs(), fast_options, repeat=5)

    outcome = result.outcomes[0]
    assert outcome.total == 5
    assert outcome.verdict == Verdict.PASS
    assert outcome.pass_k == 1.0

    verdicts = {trial.verdict for trial in outcome.trials}
    assert len(verdicts) == 1, f"harness produced inconsistent verdicts: {verdicts}"


async def test_partial_failure_is_reported_as_flaky_not_passing(fast_options):
    """A scenario that passes some repeats is flaky — never green."""
    scenario = refill_scenario(**{"assert": STANDARD_ASSERTIONS, "repeat": 3})
    async with running_agent(Bugs()) as clean_url:
        good = await execute(RunSpec(target=ws_target(clean_url), scenarios=[scenario], options=fast_options))
    async with running_agent(Bugs(skip_tool_call=True)) as bad_url:
        bad = await execute(RunSpec(target=ws_target(bad_url), scenarios=[scenario], options=fast_options))

    # Splice one failing trial into an otherwise-green outcome.
    outcome = good.outcomes[0]
    outcome.trials[0] = bad.outcomes[0].trials[0]

    assert outcome.passed == 2
    assert outcome.total == 3
    assert outcome.pass_k == pytest.approx(2 / 3)
    assert outcome.verdict == Verdict.FLAKY
    assert not good.ok, "a flaky scenario must not make the run green"


async def test_pass_k_counts_every_repeat(fast_options):
    result = await run_suite(Bugs(skip_tool_call=True), fast_options, repeat=3)
    outcome = result.outcomes[0]

    assert outcome.total == 3
    assert outcome.passed == 0
    assert outcome.pass_k == 0.0
    assert outcome.verdict == Verdict.FAIL
