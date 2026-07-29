"""Evaluation: assertions, metrics, and the honesty rules around them."""

import pytest

from convox.eval import normalize
from convox.eval.compare import ComparisonError, evaluate, percentile
from convox.eval.evaluator import evaluate_trial
from convox.model.enums import AssertionStatus, TrialStatus, Verdict
from convox.sim.runner import run_trial
from convox.testing.reference_agent import Bugs, running_agent
from tests.conftest import refill_scenario, ws_target


async def run_once(scenario, options, bugs=None):
    async with running_agent(bugs) as url:
        artifacts = await run_trial(scenario, ws_target(url), options=options)
    return evaluate_trial(artifacts, scenario)


# ─── comparison grammar ──────────────────────────────────────────────────────


def test_percentile_interpolates():
    assert percentile([100, 200, 300], 50) == 200
    assert percentile([100], 95) == 100


def test_evaluate_supports_selectors_and_operators():
    ok, actual, desc = evaluate([100, 200, 300, 400], {"p50": {"lt": 300}, "max": {"lt": 500}})
    assert ok
    assert actual["max"] == 400
    assert "p50 < 300" in desc

    ok, _, _ = evaluate([100, 900], {"max": {"lt": 500}})
    assert not ok


def test_unknown_operator_is_an_error_not_a_silent_pass():
    with pytest.raises(ComparisonError):
        evaluate([1.0], {"less_than": 5})


# ─── normalizers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("spoken", "stored"),
    [
        ("+91 98765 43210", "9876543210"),
        ("nine eight seven six five four three two one zero", "9876543210"),
        ("98765-43210", "+919876543210"),
    ],
)
def test_phone_numbers_compare_equal_across_formats(spoken, stored):
    assert normalize.e164(spoken) == normalize.e164(stored)


def test_digits_understands_spoken_numbers_in_two_languages():
    assert normalize.digits("nine eight seven") == "987"
    assert normalize.digits("nau aath saat") == "987"


# ─── assertions ──────────────────────────────────────────────────────────────


async def test_passing_scenario_is_green(fast_options):
    scenario = refill_scenario(
        **{
            "assert": [
                {"tool.called": "create_refill_order"},
                {"slot.captured": {"field": "phone", "value": "+919876543210", "normalize": "e164"}},
                {"call.no_error_frames": True},
            ]
        }
    )
    result = await run_once(scenario, fast_options)
    assert result.verdict == Verdict.PASS, [a.message for a in result.failures]


async def test_missing_tool_call_fails_and_is_attributed(fast_options):
    scenario = refill_scenario(**{"assert": [{"tool.called": "create_refill_order"}]})
    result = await run_once(scenario, fast_options, Bugs(skip_tool_call=True))

    assert result.verdict == Verdict.FAIL
    failure = result.failures[0]
    assert failure.name == "tool.called"
    assert failure.suspected_layer is not None


async def test_wrong_digit_capture_is_caught_by_ground_truth(fast_options):
    """The caller's words are known exactly, so a mis-captured digit is provable."""
    scenario = refill_scenario(
        **{
            "assert": [
                {"slot.captured": {"field": "phone", "value": "+919876543210", "normalize": "e164"}}
            ]
        }
    )
    clean = await run_once(scenario, fast_options)
    assert clean.verdict == Verdict.PASS

    mangled = await run_once(scenario, fast_options, Bugs(mangle_digits=True))
    assert mangled.verdict == Verdict.FAIL
    failure = mangled.failures[0]
    assert failure.evidence["spoken_ground_truth"], "the failure must show what the caller said"


async def test_repetition_loop_is_caught(fast_options):
    scenario = refill_scenario(**{"assert": [{"transcript.no_repetition": {"max_repeats": 2}}]})
    result = await run_once(scenario, fast_options, Bugs(repeat_loop=True))
    assert result.verdict == Verdict.FAIL
    assert "didn't catch that" in result.failures[0].evidence["phrase"]


async def test_latency_budget_catches_a_slow_agent(fast_options):
    scenario = refill_scenario(**{"assert": [{"latency.response_ms": {"max": {"lt": 400}}}]})

    fast = await run_once(scenario, fast_options)
    assert fast.verdict == Verdict.PASS

    slow = await run_once(scenario, fast_options, Bugs(slow_responses_ms=900))
    assert slow.verdict == Verdict.FAIL
    assert slow.failures[0].suspected_layer.value == "timing"


async def test_soft_assertions_record_without_failing(fast_options):
    """A soft assertion is recorded as failed but does not fail the trial."""
    scenario = refill_scenario(
        **{"assert": [{"soft": {"tool.called": "a_tool_that_does_not_exist"}}]}
    )
    result = await run_once(scenario, fast_options)

    recorded = result.assertions[0]
    assert recorded.status == AssertionStatus.FAIL
    assert recorded.soft is True
    assert result.failures == []
    assert result.verdict == Verdict.PASS


# ─── the honesty rules ───────────────────────────────────────────────────────


async def test_audio_assertions_report_unsupported_never_pass(fast_options):
    """A text channel cannot measure barge-in, and must say so rather than pass."""
    scenario = refill_scenario(
        **{"assert": [{"barge_in.stop_ms": {"max": {"lt": 300}}}, {"audio.no_truncation": True}]}
    )
    result = await run_once(scenario, fast_options)

    assert [a.status for a in result.assertions] == [
        AssertionStatus.UNSUPPORTED,
        AssertionStatus.UNSUPPORTED,
    ]
    assert result.verdict == Verdict.PASS  # unsupported does not fail the trial…
    assert len(result.unsupported) == 2  # …but it is reported, not hidden


async def test_judged_assertions_are_unsupported_without_a_backend(fast_options):
    scenario = refill_scenario(**{"assert": [{"judge": "The agent confirmed the pickup time."}]})
    result = await run_once(scenario, fast_options)

    judged = result.assertions[0]
    assert judged.status == AssertionStatus.UNSUPPORTED
    assert "judge backend" in judged.message


async def test_unknown_assertion_errors_rather_than_passing(fast_options):
    scenario = refill_scenario(**{"assert": [{"totally.made.up": True}]})
    result = await run_once(scenario, fast_options)

    assert result.assertions[0].status == AssertionStatus.ERROR
    assert result.verdict == Verdict.FAIL


async def test_infrastructure_failure_is_not_reported_as_an_agent_failure(fast_options):
    from convox.model.target import Target

    scenario = refill_scenario(**{"assert": [{"tool.called": "create_refill_order"}]})
    artifacts = await run_trial(
        scenario,
        Target(name="dead", kind="websocket", config={"url": "ws://127.0.0.1:1/nope"}),
        options=fast_options,
    )
    result = evaluate_trial(artifacts, scenario)

    assert artifacts.status == TrialStatus.INFRASTRUCTURE_ERROR
    assert result.verdict == Verdict.ERROR
    assert result.assertions == [], "assertions are meaningless against a call that never happened"


async def test_metrics_mark_unavailable_rather_than_defaulting(fast_options):
    scenario = refill_scenario()
    result = await run_once(scenario, fast_options)

    wer = result.metric("asr.wer")
    assert wer is not None
    assert wer.available is False
    assert wer.value is None
    assert "text" in wer.detail["reason"]


async def test_talk_ratio_is_flagged_as_estimated_without_audio(fast_options):
    result = await run_once(refill_scenario(), fast_options)
    ratio = result.metric("talk_ratio")
    assert ratio.detail["estimated"] is True


async def test_small_samples_are_flagged_low_confidence(fast_options):
    result = await run_once(refill_scenario(), fast_options)
    p50 = result.metric("latency.response_ms.p50")
    assert p50.detail["low_confidence"] is True
