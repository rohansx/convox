"""Metric computation.

All latency is measured as the caller experiences it — from the moment the caller
stops speaking to the moment the agent's first output arrives — because that is
what determines whether a human talks over the agent. Measuring from the end of
the agent's own transcription would flatter it by hiding endpointing delay.

Metrics whose inputs a target cannot supply are emitted with ``available=False``
and a reason, never defaulted to a passing value.
"""

from __future__ import annotations

from convox.eval.compare import percentile
from convox.model.enums import Speaker
from convox.model.trial import MetricValue, TrialArtifacts, Turn

#: A silence longer than this, with neither party speaking, counts as dead air.
DEAD_AIR_THRESHOLD_MS = 1500

#: Word length of the n-gram used for repetition detection.
REPETITION_NGRAM = 5


# ─── shared extractors (assertions use these too, so both agree by construction) ───


def response_latencies(artifacts: TrialArtifacts) -> list[tuple[Turn, int]]:
    """(agent turn, latency) for each agent turn that answered a caller turn.

    Turns the caller interrupted are excluded — those are barge-in behaviour, not
    response latency, and mixing them corrupts both measurements.
    """
    out: list[tuple[Turn, int]] = []
    previous: Turn | None = None
    for turn in artifacts.turns:
        if turn.speaker == Speaker.CALLER:
            previous = turn
            continue
        if previous is None or turn.interrupted:
            continue
        first = turn.first_audio_ms if turn.first_audio_ms is not None else turn.speech_start_ms
        latency = first - previous.speech_end_ms
        if latency >= 0:
            out.append((turn, latency))
        previous = None
    return out


def tool_response_latencies(artifacts: TrialArtifacts) -> list[int]:
    """Latency restricted to turns that involved a tool call.

    Reported separately because tool turns are structurally slower; a single budget
    either fails every tool turn or lets slow plain turns hide behind them.
    """
    return [lat for turn, lat in response_latencies(artifacts) if turn.tool_calls]


def plain_response_latencies(artifacts: TrialArtifacts) -> list[int]:
    return [lat for turn, lat in response_latencies(artifacts) if not turn.tool_calls]


def first_response_ms(artifacts: TrialArtifacts) -> int | None:
    for turn in artifacts.turns:
        if turn.speaker == Speaker.AGENT:
            return turn.first_audio_ms if turn.first_audio_ms is not None else turn.speech_start_ms
    return None


def deliberate_silences(artifacts: TrialArtifacts) -> list[tuple[int, int]]:
    """Windows where the caller chose to stay silent, so dead air can exclude them."""
    windows = []
    for event in artifacts.events:
        if event.kind == "caller.wait_silence":
            ms = int(event.payload.get("ms", 0))
            windows.append((event.at_ms, event.at_ms + ms))
    return windows


def dead_air_gaps(artifacts: TrialArtifacts) -> list[int]:
    """Silences above threshold where neither party was speaking."""
    ignored = deliberate_silences(artifacts)
    gaps: list[int] = []
    ordered = sorted(artifacts.turns, key=lambda t: t.speech_start_ms)
    for previous, following in zip(ordered, ordered[1:], strict=False):
        gap = following.speech_start_ms - previous.speech_end_ms
        if gap < DEAD_AIR_THRESHOLD_MS:
            continue
        midpoint = previous.speech_end_ms + gap // 2
        if any(start <= midpoint <= end for start, end in ignored):
            continue
        gaps.append(gap)
    return gaps


def repetition_score(artifacts: TrialArtifacts) -> tuple[int, str | None]:
    """Highest repeat count of any agent n-gram, and the offending phrase.

    Catches loop bugs ("I didn't catch that. I didn't catch that.") that pass every
    content assertion.
    """
    counts: dict[str, int] = {}
    for turn in artifacts.agent_turns:
        words = turn.text.casefold().split()
        for i in range(len(words) - REPETITION_NGRAM + 1):
            gram = " ".join(words[i : i + REPETITION_NGRAM])
            counts[gram] = counts.get(gram, 0) + 1
    if not counts:
        return 0, None
    phrase = max(counts, key=lambda k: counts[k])
    return counts[phrase], phrase


def talk_ratio(artifacts: TrialArtifacts) -> float | None:
    agent = sum(t.speech_end_ms - t.speech_start_ms for t in artifacts.agent_turns)
    caller = sum(t.speech_end_ms - t.speech_start_ms for t in artifacts.caller_turns)
    total = agent + caller
    return agent / total if total else None


# ─── metric emission ─────────────────────────────────────────────────────────


def _unavailable(name: str, reason: str, unit: str | None = None) -> MetricValue:
    return MetricValue(name=name, value=None, unit=unit, available=False, detail={"reason": reason})


def _distribution(name: str, samples: list[int], unit: str = "ms") -> list[MetricValue]:
    if not samples:
        return []
    detail = {"samples": samples, "n": len(samples)}
    #: Percentiles over a handful of turns are not trustworthy; say so rather than
    #: printing a confident number derived from four data points.
    if len(samples) < 20:
        detail["low_confidence"] = True
    return [
        MetricValue(name=f"{name}.p50", value=round(percentile(samples, 50), 1), unit=unit, detail=detail),
        MetricValue(name=f"{name}.p90", value=round(percentile(samples, 90), 1), unit=unit),
        MetricValue(name=f"{name}.p95", value=round(percentile(samples, 95), 1), unit=unit),
        MetricValue(name=f"{name}.max", value=float(max(samples)), unit=unit),
        MetricValue(name=f"{name}.mean", value=round(sum(samples) / len(samples), 1), unit=unit),
    ]


def compute(artifacts: TrialArtifacts) -> list[MetricValue]:
    metrics: list[MetricValue] = []
    has_audio = artifacts.supports("audio")

    # ── latency ──
    first = first_response_ms(artifacts)
    if first is not None:
        metrics.append(MetricValue(name="latency.first_response_ms", value=float(first), unit="ms"))

    latencies = [lat for _, lat in response_latencies(artifacts)]
    metrics.extend(_distribution("latency.response_ms", latencies))
    metrics.extend(_distribution("latency.tool_response_ms", tool_response_latencies(artifacts)))

    # ── dead air ──
    gaps = dead_air_gaps(artifacts)
    metrics.append(MetricValue(name="dead_air.count", value=float(len(gaps)), unit="count"))
    metrics.append(MetricValue(name="dead_air.max_ms", value=float(max(gaps)) if gaps else 0.0, unit="ms"))

    # ── conversation shape ──
    metrics.append(MetricValue(name="turn_count", value=float(len(artifacts.turns)), unit="count"))
    metrics.append(MetricValue(name="turn_count.agent", value=float(len(artifacts.agent_turns)), unit="count"))
    metrics.append(MetricValue(name="turn_count.caller", value=float(len(artifacts.caller_turns)), unit="count"))
    metrics.append(MetricValue(name="tool.call_count", value=float(len(artifacts.tool_calls)), unit="count"))
    metrics.append(MetricValue(name="call.duration_ms", value=float(artifacts.duration_ms), unit="ms"))

    repeats, phrase = repetition_score(artifacts)
    metrics.append(
        MetricValue(name="repetition_score", value=float(repeats), unit="count", detail={"phrase": phrase})
    )

    ratio = talk_ratio(artifacts)
    if ratio is not None:
        metrics.append(
            MetricValue(
                name="talk_ratio",
                value=round(ratio, 3),
                # Without audio the agent's speaking duration is inferred from text
                # length, so the ratio is directional rather than exact.
                detail={"estimated": not has_audio},
            )
        )

    # ── cost ──
    metrics.append(MetricValue(name="cost.test_usd", value=artifacts.cost_usd, unit="usd"))

    # ── deliberately unavailable on a text channel ──
    if not has_audio:
        reason = "target channel carries text, not audio"
        metrics += [
            _unavailable("audio.truncation_detected", reason),
            _unavailable("audio.snr_db", reason, "db"),
            _unavailable("audio.artifact_score", reason),
            _unavailable("asr.wer", reason),
            _unavailable("asr.cer", reason),
        ]
    if not artifacts.supports("barge_in"):
        metrics += [
            _unavailable("barge_in.stop_ms", "target does not expose interruption timing", "ms"),
            _unavailable("barge_in.overrun_ms", "target does not expose interruption timing", "ms"),
        ]
    return metrics
