"""Audio-layer and turn-taking assertions.

These are registered with a required capability, so against a text channel they
report ``unsupported`` rather than passing. That distinction matters: a suite that
silently "passes" barge-in checks it never ran is worse than one that admits it
could not measure them, because the first teaches a team to trust a number that
does not exist.

The measurement bodies land with the audio path; the contract is fixed now so
scenarios written today stay valid.
"""

from __future__ import annotations

from convox.eval.compare import evaluate
from convox.eval.registry import assertion, failed, ok, unsupported
from convox.model.enums import Layer
from convox.model.scenario import AssertionSpec
from convox.model.trial import TrialArtifacts


@assertion("barge_in.stop_ms", requires="barge_in")
def barge_in_stop(artifacts: TrialArtifacts, spec: AssertionSpec):
    samples = [
        e.payload["stop_ms"]
        for e in artifacts.events
        if e.kind == "barge_in.stop" and "stop_ms" in e.payload
    ]
    if not samples:
        return unsupported(spec, "no interruption occurred on this call")
    passed, actual, desc = evaluate([float(s) for s in samples], spec.value)
    if passed:
        return ok(spec, actual=actual, expected=desc, evidence={"samples": samples})
    return failed(
        spec,
        f"agent kept speaking {actual} after the caller interrupted, violates {desc}",
        actual=actual,
        expected=desc,
        layer=Layer.TIMING,
        evidence={"samples": samples},
    )


@assertion("barge_in.handled", requires="barge_in")
def barge_in_handled(artifacts: TrialArtifacts, spec: AssertionSpec):
    events = [e for e in artifacts.events if e.kind == "barge_in.stop"]
    if not events:
        return unsupported(spec, "no interruption occurred on this call")
    unhandled = [e for e in events if not e.payload.get("addressed", False)]
    if not unhandled:
        return ok(spec)
    return failed(
        spec,
        "agent resumed its previous utterance instead of addressing the interruption",
        layer=Layer.LLM,
        evidence={"at_ms": [e.at_ms for e in unhandled]},
    )


@assertion("endpoint.false_interrupt_count", requires="audio")
def false_interrupts(artifacts: TrialArtifacts, spec: AssertionSpec):
    count = sum(1 for e in artifacts.events if e.kind == "endpoint.false_interrupt")
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


@assertion("audio.no_truncation", requires="audio")
def no_truncation(artifacts: TrialArtifacts, spec: AssertionSpec):
    truncated = [e for e in artifacts.events if e.kind == "audio.truncation"]
    if not truncated:
        return ok(spec)
    return failed(
        spec,
        "the agent's final utterance was cut off mid-word",
        layer=Layer.TTS,
        evidence={"at_ms": [e.at_ms for e in truncated]},
    )


@assertion("audio.no_clipping", requires="audio")
def no_clipping(artifacts: TrialArtifacts, spec: AssertionSpec):
    clipped = [e for e in artifacts.events if e.kind == "audio.clipping"]
    if not clipped:
        return ok(spec)
    return failed(spec, "agent audio clipped", layer=Layer.TTS, evidence={"at_ms": [e.at_ms for e in clipped]})


@assertion("audio.min_snr_db", requires="audio")
def min_snr(artifacts: TrialArtifacts, spec: AssertionSpec):
    samples = [e.payload["snr_db"] for e in artifacts.events if e.kind == "audio.snr"]
    if not samples:
        return unsupported(spec, "no SNR measurements recorded")
    passed, actual, desc = evaluate([float(s) for s in samples], spec.value)
    return (
        ok(spec, actual=actual, expected=desc)
        if passed
        else failed(spec, f"SNR {actual} violates {desc}", actual=actual, expected=desc, layer=Layer.TTS)
    )


@assertion("asr.wer", requires="audio")
def asr_wer(artifacts: TrialArtifacts, spec: AssertionSpec):
    """Word error rate of the agent's recognition, against known ground truth.

    Requires both an acoustic path and the platform's own transcript of what it
    heard — without the latter there is nothing to diff the ground truth against.
    """
    if not artifacts.supports("agent_transcript"):
        return unsupported(spec, "target does not expose its transcript of the caller")
    pairs = [(t.ground_truth_text, t.heard_text) for t in artifacts.caller_turns if t.heard_text]
    if not pairs:
        return unsupported(spec, "no agent-side transcript of caller turns was captured")
    return unsupported(spec, "WER scoring lands with the audio path")
