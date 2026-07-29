"""Audio-layer and turn-taking assertions.

These are the checks that only exist once there is sound. They are registered with
a required capability, so against a text channel they report ``unsupported``
rather than passing — a suite that silently "passes" barge-in checks it never ran
is worse than one that admits it could not measure them, because the first teaches
a team to trust a number that does not exist.
"""

from __future__ import annotations

from convox.eval.compare import evaluate
from convox.eval.metrics import (
    BARGE_IN_GRACE_MS,
    barge_in_stops,
    recognition_pairs,
    unhandled_barge_ins,
)
from convox.eval.registry import assertion, failed, ok, unsupported
from convox.eval.wer import cer, entity_error_rate, wer
from convox.model.enums import Layer
from convox.model.scenario import AssertionSpec
from convox.model.trial import TrialArtifacts


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


# ─── interruption handling ───────────────────────────────────────────────────


@assertion("barge_in.stop_ms", requires="barge_in")
def barge_in_stop(artifacts: TrialArtifacts, spec: AssertionSpec):
    """How long the agent kept talking after the caller cut in."""
    stops = barge_in_stops(artifacts)
    if not stops and unhandled_barge_ins(artifacts):
        return failed(
            spec,
            "agent never stopped for the interruption — it talked straight through",
            layer=Layer.TIMING,
        )
    if not stops:
        return unsupported(spec, "no interruption occurred on this call")

    passed, actual, desc = evaluate([float(s) for s in stops], spec.value)
    if passed:
        return ok(spec, actual=actual, expected=desc, evidence={"samples": stops})
    return failed(
        spec,
        f"agent kept speaking {actual} after the caller interrupted, violates {desc}",
        actual=actual,
        expected=desc,
        layer=Layer.TIMING,
        evidence={"samples": stops},
    )


@assertion("barge_in.handled", requires="barge_in")
def barge_in_handled(artifacts: TrialArtifacts, spec: AssertionSpec):
    if artifacts.audio is None or not artifacts.audio.barge_ins:
        return unsupported(spec, "no interruption occurred on this call")

    unhandled = [
        b
        for b in artifacts.audio.barge_ins
        if b.stop_ms is None or b.stop_ms > BARGE_IN_GRACE_MS
    ]
    if not unhandled:
        return ok(
            spec,
            evidence={
                "interruptions": len(artifacts.audio.barge_ins),
                "stop_ms": [b.stop_ms for b in artifacts.audio.barge_ins],
            },
        )
    kept_going = [b.stop_ms for b in unhandled]
    return failed(
        spec,
        "agent talked through the interruption instead of yielding "
        f"(kept speaking {kept_going}ms after the caller cut in)",
        layer=Layer.TIMING,
        evidence={"at_ms": [b.caller_start_ms for b in unhandled], "stop_ms": kept_going},
    )


@assertion("barge_in.overrun_ms", requires="barge_in")
def barge_in_overrun(artifacts: TrialArtifacts, spec: AssertionSpec):
    """Audio that arrived after the caller began speaking.

    Distinct from stop time: some stacks stop generating but keep flushing a
    buffer, which the caller still hears.
    """
    if artifacts.audio is None or not artifacts.audio.barge_ins:
        return unsupported(spec, "no interruption occurred on this call")
    samples = [float(b.overrun_ms) for b in artifacts.audio.barge_ins]
    passed, actual, desc = evaluate(samples, spec.value)
    return (
        ok(spec, actual=actual, expected=desc)
        if passed
        else failed(spec, f"TTS overrun {actual} violates {desc}", actual=actual, expected=desc, layer=Layer.TTS)
    )


@assertion("endpoint.false_interrupt_count", requires="audio")
def false_interrupts(artifacts: TrialArtifacts, spec: AssertionSpec):
    """Times the agent started talking while the caller was still mid-utterance."""
    count = sum(
        1
        for turn in artifacts.agent_turns
        if any(
            caller.speech_start_ms < turn.speech_start_ms < caller.speech_end_ms
            for caller in artifacts.caller_turns
            if not caller.interrupted
        )
    )
    passed, actual, desc = evaluate([float(count)], spec.value)
    if passed:
        return ok(spec, actual=count, expected=desc)
    return failed(
        spec,
        f"agent talked over the caller {count} time(s), violates {desc}",
        actual=count,
        expected=desc,
        layer=Layer.TIMING,
    )


# ─── audio quality ───────────────────────────────────────────────────────────


@assertion("audio.no_truncation", requires="audio")
def no_truncation(artifacts: TrialArtifacts, spec: AssertionSpec):
    """Did the agent's audio stop mid-word?

    Entirely invisible in a transcript: the text was generated in full even though
    the caller never heard the end of it.
    """
    if artifacts.audio is None or artifacts.audio.agent is None:
        return unsupported(spec, "no agent audio was captured")
    if not artifacts.audio.agent.truncated:
        return ok(spec)
    return failed(
        spec,
        "the agent's audio was cut off mid-utterance rather than finishing",
        layer=Layer.TTS,
        evidence={"duration_ms": artifacts.audio.agent.duration_ms},
    )


@assertion("audio.no_clipping", requires="audio")
def no_clipping(artifacts: TrialArtifacts, spec: AssertionSpec):
    if artifacts.audio is None or artifacts.audio.agent is None:
        return unsupported(spec, "no agent audio was captured")
    ratio = artifacts.audio.agent.clipping_ratio
    limit = float(_as_dict(spec.value).get("max_ratio", 0.001))
    if ratio <= limit:
        return ok(spec, actual=ratio, expected=f"<= {limit}")
    return failed(spec, f"agent audio clipped on {ratio:.2%} of samples", actual=ratio, layer=Layer.TTS)


@assertion("audio.no_artifacts", requires="audio")
def no_artifacts(artifacts: TrialArtifacts, spec: AssertionSpec):
    """Detects garbled synthesis — a burst of noise where speech should be."""
    if artifacts.audio is None or artifacts.audio.agent is None:
        return unsupported(spec, "no agent audio was captured")
    score = artifacts.audio.agent.artifact_score
    limit = float(_as_dict(spec.value).get("max_score", 0.35))
    if score <= limit:
        return ok(spec, actual=score, expected=f"<= {limit}")
    return failed(
        spec,
        f"agent audio contains synthesis artefacts (score {score:.2f} > {limit})",
        actual=score,
        layer=Layer.TTS,
    )


@assertion("audio.min_snr_db", requires="audio")
def min_snr(artifacts: TrialArtifacts, spec: AssertionSpec):
    if artifacts.audio is None or artifacts.audio.agent is None:
        return unsupported(spec, "no agent audio was captured")
    snr = artifacts.audio.agent.snr_db
    if snr is None:
        return unsupported(spec, "SNR could not be estimated from this audio")
    passed, actual, desc = evaluate([snr], spec.value)
    return (
        ok(spec, actual=actual, expected=desc)
        if passed
        else failed(spec, f"SNR {actual} violates {desc}", actual=actual, expected=desc, layer=Layer.TTS)
    )


@assertion("audio.silence_ratio", requires="audio")
def silence_ratio(artifacts: TrialArtifacts, spec: AssertionSpec):
    if artifacts.audio is None or artifacts.audio.agent is None:
        return unsupported(spec, "no agent audio was captured")
    passed, actual, desc = evaluate([artifacts.audio.agent.silence_ratio], spec.value)
    return (
        ok(spec, actual=actual, expected=desc)
        if passed
        else failed(spec, f"silence ratio {actual} violates {desc}", actual=actual, expected=desc)
    )


# ─── recognition accuracy, against known ground truth ────────────────────────


def _recognition_assertion(artifacts, spec, scorer, label):
    pairs = recognition_pairs(artifacts)
    if not pairs:
        return unsupported(spec, "target did not expose its transcript of the caller")

    reference = " ".join(p[0] for p in pairs)
    heard = " ".join(p[1] for p in pairs)
    alignment = scorer(reference, heard)

    passed, actual, desc = evaluate([alignment.error_rate], spec.value)
    evidence = {
        "reference": reference[:400],
        "agent_heard": heard[:400],
        "errors": [list(e) for e in alignment.errors][:20],
    }
    if passed:
        return ok(spec, actual=round(alignment.error_rate, 4), expected=desc, evidence=evidence)
    return failed(
        spec,
        f"{label} {alignment.error_rate:.1%} violates {desc}",
        actual=round(alignment.error_rate, 4),
        expected=desc,
        # The caller's words were correct by construction, so a mismatch is the
        # agent's recognition, not its reasoning.
        layer=Layer.STT,
        evidence=evidence,
    )


@assertion("asr.wer", requires="audio")
def asr_wer(artifacts: TrialArtifacts, spec: AssertionSpec):
    """Word error rate of the agent's recognition against what the caller said."""
    return _recognition_assertion(
        artifacts, spec, lambda r, h: wer(r, h, artifacts.language), "word error rate"
    )


@assertion("asr.cer", requires="audio")
def asr_cer(artifacts: TrialArtifacts, spec: AssertionSpec):
    return _recognition_assertion(
        artifacts, spec, lambda r, h: cer(r, h, artifacts.language), "character error rate"
    )


@assertion("asr.entity_error_rate", requires="audio")
def asr_entity_error_rate(artifacts: TrialArtifacts, spec: AssertionSpec):
    """Error rate over the values that actually change the outcome.

    A phone number lost to a mishearing matters; a dropped filler word does not.
    A single overall rate cannot tell those apart.
    """
    entities = [str(v) for v in artifacts.facts.values()] or None
    return _recognition_assertion(
        artifacts, spec, lambda r, h: entity_error_rate(r, h, entities), "entity error rate"
    )
