"""Built-in deterministic assertions.

Deterministic by default is a design stance, not a convenience: no model in the
loop means no run-to-run variance, and a failure that is always the same failure.
Judged assertions exist for genuinely semantic claims and live in ``judges.py``.
"""

from __future__ import annotations

import re
from typing import Any

from convox.eval import normalize
from convox.eval.compare import ComparisonError, evaluate
from convox.eval.metrics import (
    dead_air_gaps,
    first_response_ms,
    plain_response_latencies,
    repetition_score,
    response_latencies,
    tool_response_latencies,
)
from convox.eval.registry import assertion, errored, failed, ok, unsupported
from convox.model.enums import EndedBy, Layer, Speaker
from convox.model.scenario import AssertionSpec
from convox.model.trial import TrialArtifacts


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"text": value}


def _speaker(value: str | None) -> Speaker | None:
    if value in (None, "any"):
        return None
    return Speaker(value)


def _transcript(artifacts: TrialArtifacts, speaker: str | None) -> str:
    return artifacts.transcript_text(_speaker(speaker))


def _turn_evidence(artifacts: TrialArtifacts, needle: str, speaker: str | None) -> list[int]:
    target = _speaker(speaker)
    return [
        t.index
        for t in artifacts.turns
        if (target is None or t.speaker == target) and normalize.contains(t.text, needle)
    ]


# ─── transcript ──────────────────────────────────────────────────────────────


@assertion("transcript.contains")
def transcript_contains(artifacts: TrialArtifacts, spec: AssertionSpec):
    cfg = _as_dict(spec.value)
    needle = str(cfg["text"])
    speaker = cfg.get("speaker")
    turns = _turn_evidence(artifacts, needle, speaker)
    if turns:
        return ok(spec, expected=needle, evidence={"turns": turns})
    return failed(
        spec,
        f"never said by {speaker or 'either party'}: {needle!r}",
        expected=needle,
        layer=Layer.LLM,
        evidence={"searched_speaker": speaker or "any"},
    )


@assertion("transcript.not_contains")
def transcript_not_contains(artifacts: TrialArtifacts, spec: AssertionSpec):
    cfg = _as_dict(spec.value)
    needle = str(cfg["text"])
    speaker = cfg.get("speaker")
    turns = _turn_evidence(artifacts, needle, speaker)
    if not turns:
        return ok(spec, expected=f"absent: {needle}")
    return failed(spec, f"forbidden phrase present: {needle!r}", layer=Layer.LLM, evidence={"turns": turns})


@assertion("transcript.matches")
def transcript_matches(artifacts: TrialArtifacts, spec: AssertionSpec):
    cfg = _as_dict(spec.value)
    pattern = str(cfg.get("pattern", cfg.get("text", "")))
    speaker = cfg.get("speaker")
    match = re.search(pattern, _transcript(artifacts, speaker), re.IGNORECASE)
    if match:
        return ok(spec, expected=pattern, actual=match.group(0), evidence={"groups": match.groupdict()})
    return failed(spec, f"pattern never matched: {pattern!r}", expected=pattern, layer=Layer.LLM)


@assertion("transcript.order")
def transcript_order(artifacts: TrialArtifacts, spec: AssertionSpec):
    phrases = list(spec.value or [])
    haystack = normalize.basic(_transcript(artifacts, "agent"))
    cursor = 0
    for phrase in phrases:
        found = haystack.find(normalize.basic(str(phrase)), cursor)
        if found < 0:
            return failed(
                spec,
                f"expected {phrase!r} after the preceding phrases, but it never appeared in order",
                expected=phrases,
                layer=Layer.LLM,
            )
        cursor = found + 1
    return ok(spec, expected=phrases)


@assertion("transcript.no_repetition")
def transcript_no_repetition(artifacts: TrialArtifacts, spec: AssertionSpec):
    cfg = _as_dict(spec.value) if isinstance(spec.value, dict) else {}
    limit = int(cfg.get("max_repeats", 2))
    count, phrase = repetition_score(artifacts)
    if count <= limit:
        return ok(spec, actual=count, expected=f"<= {limit}")
    return failed(
        spec,
        f"agent repeated {phrase!r} {count} times (limit {limit})",
        actual=count,
        expected=f"<= {limit}",
        layer=Layer.LLM,
        evidence={"phrase": phrase},
    )


@assertion("turn_count")
def turn_count(artifacts: TrialArtifacts, spec: AssertionSpec):
    passed, actual, desc = evaluate([float(len(artifacts.turns))], spec.value)
    return (
        ok(spec, actual=actual, expected=desc)
        if passed
        else failed(spec, f"turn count {actual['mean']:.0f} violates {desc}", actual=actual, expected=desc)
    )


# ─── slots (ground-truth powered) ────────────────────────────────────────────


@assertion("slot.captured")
def slot_captured(artifacts: TrialArtifacts, spec: AssertionSpec):
    """Did the agent capture a value the caller actually spoke?

    Because Convox emitted the caller's words, the expected value is known exactly.
    When the agent got it wrong we can also say *why*: if the agent's own
    transcript already differed from what we said, the fault is recognition rather
    than reasoning.
    """
    cfg = _as_dict(spec.value)
    field = str(cfg["field"])
    expected = str(cfg.get("value", artifacts.facts.get(field, "")))
    normalizer = normalize.get(cfg.get("normalize"))
    wanted = normalizer(expected)

    if not wanted:
        return errored(spec, f"no expected value for slot {field!r}")

    spoken = next(
        (t.ground_truth_text for t in artifacts.caller_turns if normalizer(t.text) and wanted in normalizer(t.text)),
        None,
    )

    for call in artifacts.tool_calls:
        for key, value in call.args.items():
            if normalizer(str(value)) == wanted:
                return ok(
                    spec,
                    actual=value,
                    expected=expected,
                    evidence={"source": f"tool:{call.name}.{key}", "spoken_ground_truth": spoken},
                )

    for turn in artifacts.agent_turns:
        if wanted and wanted in normalizer(turn.text):
            return ok(
                spec,
                actual=turn.text,
                expected=expected,
                evidence={"source": "agent_readback", "turn": turn.index, "spoken_ground_truth": spoken},
            )

    # Attribute the failure: if the agent never heard it correctly, that is STT.
    layer = Layer.STT if artifacts.supports("audio") else Layer.LLM
    return failed(
        spec,
        f"agent never captured {field!r} (expected {expected!r})",
        expected=expected,
        layer=layer,
        evidence={
            "spoken_ground_truth": spoken,
            "agent_transcript": artifacts.transcript_text(Speaker.AGENT)[:500],
            "tool_args": [c.args for c in artifacts.tool_calls],
        },
    )


# ─── tools ───────────────────────────────────────────────────────────────────


@assertion("tool.called", requires="tool_calls")
def tool_called(artifacts: TrialArtifacts, spec: AssertionSpec):
    name = str(spec.value)
    matches = [c for c in artifacts.tool_calls if c.name == name]
    if matches:
        return ok(spec, expected=name, evidence={"at_ms": [c.at_ms for c in matches]})
    return failed(
        spec,
        f"tool {name!r} was never called",
        expected=name,
        layer=Layer.TOOL,
        actual=[c.name for c in artifacts.tool_calls],
    )


@assertion("tool.not_called", requires="tool_calls")
def tool_not_called(artifacts: TrialArtifacts, spec: AssertionSpec):
    name = str(spec.value)
    matches = [c for c in artifacts.tool_calls if c.name == name]
    if not matches:
        return ok(spec, expected=f"absent: {name}")
    return failed(spec, f"tool {name!r} was called {len(matches)} time(s)", layer=Layer.TOOL, actual=len(matches))


@assertion("tool.call_count", requires="tool_calls")
def tool_call_count(artifacts: TrialArtifacts, spec: AssertionSpec):
    cfg = _as_dict(spec.value)
    name = cfg.get("tool")
    calls = [c for c in artifacts.tool_calls if name is None or c.name == name]
    expr = {k: v for k, v in cfg.items() if k != "tool"}
    passed, actual, desc = evaluate([float(len(calls))], expr)
    return (
        ok(spec, actual=actual, expected=desc)
        if passed
        else failed(spec, f"{name or 'tool'} call count {len(calls)} violates {desc}", actual=len(calls), expected=desc)
    )


@assertion("tool.called_with", requires="tool_calls")
def tool_called_with(artifacts: TrialArtifacts, spec: AssertionSpec):
    cfg = _as_dict(spec.value)
    name = str(cfg["tool"])
    wanted: dict[str, Any] = dict(cfg.get("args") or {})
    mode = cfg.get("match", "subset")

    candidates = [c for c in artifacts.tool_calls if c.name == name]
    if not candidates:
        return failed(spec, f"tool {name!r} was never called", expected=wanted, layer=Layer.TOOL)

    for call in candidates:
        if mode == "exact":
            if call.args == wanted:
                return ok(spec, actual=call.args, expected=wanted)
        elif all(normalize.basic(str(call.args.get(k))) == normalize.basic(str(v)) for k, v in wanted.items()):
            return ok(spec, actual=call.args, expected=wanted)

    return failed(
        spec,
        f"tool {name!r} was called, but never with the expected arguments",
        expected=wanted,
        actual=[c.args for c in candidates],
        layer=Layer.LLM,
    )


@assertion("tool.call_order", requires="tool_calls")
def tool_call_order(artifacts: TrialArtifacts, spec: AssertionSpec):
    wanted = [str(n) for n in (spec.value or [])]
    actual = [c.name for c in artifacts.tool_calls]
    cursor = 0
    for position, name in enumerate(wanted):
        if name not in actual[cursor:]:
            preceding = wanted[:position]
            return failed(
                spec,
                f"expected {name!r} after {preceding}",
                expected=wanted,
                actual=actual,
                layer=Layer.TOOL,
            )
        cursor = actual.index(name, cursor) + 1
    return ok(spec, expected=wanted, actual=actual)


# ─── timing ──────────────────────────────────────────────────────────────────


def _latency_assertion(artifacts, spec, samples, label):
    if not samples:
        return unsupported(spec, f"no {label} turns to measure")
    try:
        passed, actual, desc = evaluate([float(s) for s in samples], spec.value)
    except ComparisonError as exc:
        return errored(spec, str(exc))
    evidence = {"samples": samples, "n": len(samples)}
    if passed:
        return ok(spec, actual=actual, expected=desc, evidence=evidence)
    return failed(
        spec,
        f"{label} latency {actual} violates {desc}",
        actual=actual,
        expected=desc,
        layer=Layer.TIMING,
        evidence=evidence,
    )


@assertion("latency.response_ms")
def latency_response(artifacts: TrialArtifacts, spec: AssertionSpec):
    return _latency_assertion(artifacts, spec, [lat for _, lat in response_latencies(artifacts)], "response")


@assertion("latency.plain_response_ms")
def latency_plain_response(artifacts: TrialArtifacts, spec: AssertionSpec):
    return _latency_assertion(artifacts, spec, plain_response_latencies(artifacts), "non-tool response")


@assertion("latency.tool_response_ms")
def latency_tool_response(artifacts: TrialArtifacts, spec: AssertionSpec):
    return _latency_assertion(artifacts, spec, tool_response_latencies(artifacts), "tool response")


@assertion("latency.first_response_ms")
def latency_first_response(artifacts: TrialArtifacts, spec: AssertionSpec):
    value = first_response_ms(artifacts)
    if value is None:
        return failed(spec, "the agent never spoke", layer=Layer.TIMING)
    return _latency_assertion(artifacts, spec, [value], "first response")


@assertion("dead_air.max_ms")
def dead_air_max(artifacts: TrialArtifacts, spec: AssertionSpec):
    gaps = dead_air_gaps(artifacts)
    longest = max(gaps) if gaps else 0
    passed, actual, desc = evaluate([float(longest)], spec.value)
    if passed:
        return ok(spec, actual=longest, expected=desc)
    return failed(
        spec,
        f"longest dead air {longest}ms violates {desc}",
        actual=longest,
        expected=desc,
        layer=Layer.TIMING,
        evidence={"gaps": gaps},
    )


@assertion("dead_air.count")
def dead_air_count(artifacts: TrialArtifacts, spec: AssertionSpec):
    gaps = dead_air_gaps(artifacts)
    passed, actual, desc = evaluate([float(len(gaps))], spec.value)
    if passed:
        return ok(spec, actual=len(gaps), expected=desc)
    return failed(
        spec,
        f"{len(gaps)} dead-air gap(s) violates {desc}",
        actual=len(gaps),
        expected=desc,
        layer=Layer.TIMING,
        evidence={"gaps": gaps},
    )


# ─── call lifecycle ──────────────────────────────────────────────────────────


@assertion("call.ended_by")
def call_ended_by(artifacts: TrialArtifacts, spec: AssertionSpec):
    expected = EndedBy(str(spec.value))
    if artifacts.ended_by == expected:
        return ok(spec, actual=artifacts.ended_by, expected=expected)
    return failed(
        spec,
        f"call ended by {artifacts.ended_by}, expected {expected}",
        actual=artifacts.ended_by,
        expected=expected,
    )


@assertion("call.duration_s")
def call_duration(artifacts: TrialArtifacts, spec: AssertionSpec):
    seconds = artifacts.duration_ms / 1000
    passed, actual, desc = evaluate([seconds], spec.value)
    return (
        ok(spec, actual=round(seconds, 2), expected=desc)
        if passed
        else failed(spec, f"call lasted {seconds:.1f}s, violates {desc}", actual=round(seconds, 2), expected=desc)
    )


@assertion("call.no_error_frames")
def call_no_error_frames(artifacts: TrialArtifacts, spec: AssertionSpec):
    errors = [e for e in artifacts.events if e.kind == "error"]
    if not errors:
        return ok(spec)
    return failed(
        spec,
        f"{len(errors)} error frame(s) during the call",
        actual=[e.payload.get("message") for e in errors],
    )


# ─── safety and compliance ───────────────────────────────────────────────────

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "card_number": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
}


@assertion("pii.not_leaked")
def pii_not_leaked(artifacts: TrialArtifacts, spec: AssertionSpec):
    """Check the agent never volunteered PII the caller did not already provide."""
    wanted = [str(p) for p in (spec.value or [])]
    agent_text = artifacts.transcript_text(Speaker.AGENT)
    caller_text = artifacts.transcript_text(Speaker.CALLER)

    leaks: list[dict[str, str]] = []
    for label in wanted:
        pattern = PII_PATTERNS.get(label)
        if pattern is None:
            continue
        for match in pattern.findall(agent_text):
            value = match if isinstance(match, str) else match[0]
            # Repeating back what the caller just said is not a leak.
            if normalize.digits(value) and normalize.digits(value) in normalize.digits(caller_text):
                continue
            leaks.append({"kind": label, "value": value})

    unknown = [label for label in wanted if label not in PII_PATTERNS]
    if leaks:
        return failed(spec, f"agent disclosed {len(leaks)} PII value(s)", actual=leaks, layer=Layer.LLM)
    if unknown and not wanted:
        return unsupported(spec, f"no pattern defined for: {', '.join(unknown)}")
    return ok(spec, expected=wanted, evidence={"unchecked_patterns": unknown})


@assertion("compliance.disclosure_present")
def disclosure_present(artifacts: TrialArtifacts, spec: AssertionSpec):
    cfg = _as_dict(spec.value)
    needle = str(cfg["text"])
    within_s = cfg.get("within_s")
    for turn in artifacts.agent_turns:
        if normalize.contains(turn.text, needle):
            if within_s is not None and turn.speech_start_ms > within_s * 1000:
                return failed(
                    spec,
                    f"disclosure came at {turn.speech_start_ms / 1000:.1f}s, required within {within_s}s",
                    actual=turn.speech_start_ms,
                    layer=Layer.LLM,
                    evidence={"turn": turn.index},
                )
            return ok(spec, expected=needle, evidence={"turn": turn.index, "at_ms": turn.speech_start_ms})
    return failed(spec, f"required disclosure never spoken: {needle!r}", expected=needle, layer=Layer.LLM)
