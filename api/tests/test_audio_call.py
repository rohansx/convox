"""End-to-end calls over a real audio channel.

These are the tests that justify the audio path existing: every failure here is one
that a transcript-level tool cannot see at all.
"""

import pytest

from convox.eval.evaluator import evaluate_trial
from convox.model.enums import AssertionStatus, TrialStatus, Verdict
from convox.model.persona import ChannelConfig, EnvironmentConfig, Persona
from convox.model.scenario import Say, Scenario
from convox.model.target import Target
from convox.sim.runner import RunOptions, run_trial
from convox.testing.audio_agent import AudioBugs, running_audio_agent

pytestmark = pytest.mark.audio


def audio_target(url: str) -> Target:
    return Target(name="audio", kind="websocket_audio", config={"url": url, "sample_rate": 16000})


def audio_scenario(assertions=None, script=None, **overrides) -> Scenario:
    data = {
        "name": "audio_refill",
        "caller": {
            "facts": {"phone": "+91 98765 43210"},
            "opening": "hi i need a refill",
            "script": script
            or [
                Say(text="nine eight seven six five four three two one zero"),
                Say(text="metformin"),
                Say(text="no thats all"),
            ],
        },
        "assert": assertions or [],
        "max_turns": 8,
        "timeout_s": 120,
    }
    data.update(overrides)
    return Scenario.model_validate(data)


async def place_call(bugs=None, *, scenario=None, persona=None):
    scenario = scenario or audio_scenario()
    async with running_audio_agent(bugs) as url:
        artifacts = await run_trial(
            scenario, audio_target(url), persona=persona, options=RunOptions(realtime=True)
        )
    return artifacts, evaluate_trial(artifacts, scenario)


# ─── the channel works ───────────────────────────────────────────────────────


async def test_a_spoken_conversation_completes():
    artifacts, _ = await place_call()

    assert artifacts.status == TrialStatus.COMPLETED
    assert artifacts.supports("audio") is True
    assert len(artifacts.agent_turns) >= 3
    # Convox transcribes the agent independently of anything the platform offers.
    assert all(t.convox_transcript for t in artifacts.agent_turns)
    assert artifacts.audio is not None
    assert artifacts.audio.agent.duration_ms > 0


async def test_digits_spoken_aloud_are_captured_by_the_agent():
    """The full loop: synthesis → channel → the agent's own ASR → a tool call."""
    artifacts, _ = await place_call()
    calls = {c.name: c.args for c in artifacts.tool_calls}

    assert "create_refill_order" in calls
    assert calls["lookup_patient"]["phone"] == "9876543210"


async def test_recognition_is_scored_against_known_ground_truth():
    artifacts, result = await place_call()

    word_error = result.metric("asr.wer")
    assert word_error is not None and word_error.available
    assert word_error.value == 0.0, word_error.detail.get("errors")
    assert artifacts.supports("agent_transcript") is True


# ─── failures only audio can reveal ──────────────────────────────────────────


async def test_truncated_audio_is_caught_though_the_transcript_is_perfect():
    scenario = audio_scenario([{"audio.no_truncation": True}])
    artifacts, result = await place_call(AudioBugs(truncate_audio=True), scenario=scenario)

    assert result.verdict == Verdict.FAIL
    failure = result.failures[0]
    assert failure.name == "audio.no_truncation"
    assert failure.suspected_layer.value == "tts"
    # The words were all generated — only the sound was cut.
    assert artifacts.agent_turns


async def test_clean_agent_audio_is_not_reported_as_truncated():
    scenario = audio_scenario([{"audio.no_truncation": True}])
    _, result = await place_call(scenario=scenario)
    assert result.verdict == Verdict.PASS, [f.message for f in result.failures]


async def test_synthesis_artifacts_are_detected():
    scenario = audio_scenario([{"audio.no_artifacts": {"max_score": 0.3}}])
    _, result = await place_call(AudioBugs(tts_artifact=True), scenario=scenario)

    assert result.verdict == Verdict.FAIL
    assert result.failures[0].suspected_layer.value == "tts"


async def test_an_agent_that_ignores_interruptions_is_caught():
    """Barge-in handling — invisible in a transcript, obvious to a caller."""
    script = [
        Say(text="nine eight seven six five four three two one zero"),
        {"barge_in": {"after_ms": 400, "say": "wait stop"}},
    ]
    scenario = audio_scenario([{"barge_in.handled": True}], script=script)

    _, polite = await place_call(scenario=scenario)
    assert polite.verdict == Verdict.PASS, [f.message for f in polite.failures]

    _, rude = await place_call(AudioBugs(ignore_barge_in=True), scenario=scenario)
    assert rude.verdict == Verdict.FAIL
    assert "talked through" in rude.failures[0].message


async def test_barge_in_stop_time_is_measured():
    script = [
        Say(text="nine eight seven six five four three two one zero"),
        {"barge_in": {"after_ms": 400, "say": "wait stop"}},
    ]
    scenario = audio_scenario(script=script)
    artifacts, result = await place_call(scenario=scenario)

    assert artifacts.audio.barge_ins, "the interruption was never recorded"
    stop = result.metric("barge_in.stop_ms.max")
    assert stop is not None and stop.value is not None
    assert stop.value >= 0


# ─── noise degrades recognition, and the report says so ──────────────────────


async def test_heavy_noise_degrades_recognition_end_to_end():
    """A caller on a loud street is a different agent-facing problem entirely."""
    noisy = Persona(
        name="noisy_street",
        environment=EnvironmentConfig(noise_profile="street_traffic_india", snr_db=2),
        channel=ChannelConfig(codec="g711u", sample_rate=16000),
    )
    scenario = audio_scenario([{"asr.wer": {"lt": 0.05}}])

    _, clean = await place_call(scenario=scenario)
    assert clean.verdict == Verdict.PASS, [f.message for f in clean.failures]

    _, degraded = await place_call(scenario=scenario, persona=noisy)
    failure = next((f for f in degraded.failures if f.name == "asr.wer"), None)
    assert failure is not None, "noise did not surface as a recognition failure"
    # The caller's words were correct by construction, so this is the agent's ASR.
    assert failure.suspected_layer.value == "stt"
    assert failure.evidence["errors"], "a WER failure must show which words were lost"


async def test_channel_damage_is_recorded_on_the_timeline():
    noisy = Persona(
        name="noisy",
        environment=EnvironmentConfig(noise_profile="cafe", snr_db=10),
        channel=ChannelConfig(codec="g711u", network="4g_congested", sample_rate=16000),
    )
    artifacts, _ = await place_call(persona=noisy)

    damage = [e for e in artifacts.events if e.kind == "channel.damage"]
    assert damage, "the channel simulator did not record what it did"
    assert damage[0].payload["codec"] == "g711u"
    assert damage[0].payload["noise_profile"] == "cafe"


# ─── honesty still holds on this channel ─────────────────────────────────────


async def test_judged_assertions_remain_unsupported_on_an_audio_call():
    scenario = audio_scenario([{"judge": "The agent confirmed the pickup time."}])
    _, result = await place_call(scenario=scenario)
    assert result.assertions[0].status == AssertionStatus.UNSUPPORTED
