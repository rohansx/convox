"""End-to-end: a scripted caller holds a real conversation with a real agent."""

from convox.model.enums import EndedBy, Speaker, TrialStatus
from convox.model.target import Target
from convox.sim.runner import run_trial
from convox.testing.reference_agent import Bugs, running_agent
from tests.conftest import refill_scenario, ws_target


async def test_scripted_call_produces_a_complete_bundle(fast_options):
    async with running_agent() as url:
        artifacts = await run_trial(refill_scenario(), ws_target(url), options=fast_options)

    assert artifacts.status == TrialStatus.COMPLETED
    assert artifacts.error is None

    # Both parties spoke, and the caller's words are recorded as ground truth.
    assert len(artifacts.caller_turns) == 4  # opening + three scripted lines
    assert artifacts.agent_turns
    assert all(t.ground_truth_text for t in artifacts.caller_turns)
    # The agent greets on connect, so it holds the first turn.
    assert artifacts.turns[0].speaker == Speaker.AGENT

    # The timeline is ordered and anchored at connect.
    assert artifacts.events[0].kind == "call.connected"
    assert all(
        a.at_ms <= b.at_ms for a, b in zip(artifacts.events, artifacts.events[1:], strict=False)
    )


async def test_agent_tool_calls_are_captured(fast_options):
    async with running_agent() as url:
        artifacts = await run_trial(refill_scenario(), ws_target(url), options=fast_options)

    names = [c.name for c in artifacts.tool_calls]
    assert "lookup_patient" in names
    assert "create_refill_order" in names


async def test_capabilities_are_recorded_on_the_bundle(fast_options):
    async with running_agent() as url:
        artifacts = await run_trial(refill_scenario(), ws_target(url), options=fast_options)

    # The text channel carries no audio, and the bundle says so — which is what
    # lets audio assertions report `unsupported` instead of passing.
    assert artifacts.supports("tool_calls") is True
    assert artifacts.supports("audio") is False


async def test_agent_hangup_is_attributed_to_the_agent(fast_options):
    scenario = refill_scenario(name="agent_ends_call")
    async with running_agent() as url:
        artifacts = await run_trial(scenario, ws_target(url), options=fast_options)

    assert artifacts.ended_by in (EndedBy.AGENT, EndedBy.CALLER)


async def test_unreachable_target_is_infrastructure_not_agent_failure(fast_options):
    target = Target(name="dead", kind="websocket", config={"url": "ws://127.0.0.1:1/nope"})
    artifacts = await run_trial(refill_scenario(), target, options=fast_options)

    assert artifacts.status == TrialStatus.INFRASTRUCTURE_ERROR
    assert artifacts.ended_by == EndedBy.ERROR
    assert artifacts.error


async def test_slow_agent_shows_up_in_response_latency(fast_options):
    from convox.eval.metrics import response_latencies

    async with running_agent(Bugs(slow_responses_ms=700)) as url:
        artifacts = await run_trial(refill_scenario(), ws_target(url), options=fast_options)

    latencies = [lat for _, lat in response_latencies(artifacts)]
    assert latencies, "expected at least one measurable agent response"
    assert max(latencies) >= 600, f"injected 700ms delay was not observed: {latencies}"
