# Convox — Metrics Catalog

> Last updated: July 2026
> Status: Pre-implementation design
> Related: [Features](features.md) · [Scenario Spec](scenario-spec.md) · [Tech Spec](tech-spec.md)

Every metric here is computed on every trial (and on every ingested production call where the data supports it). This document is the normative definition — if a number appears in the dashboard, its formula is here.

**Availability principle.** A metric that cannot be computed on a given target is reported as `unavailable`, never defaulted to a passing value. A run states how many metrics were unmeasurable and why.

---

## 1. Measurement foundations

### 1.1 The timeline

Everything derives from a single event timeline with 1 ms resolution, stamped from a monotonic clock at the simulation worker:

```
t=0        call_connected
t=340      agent_speech_start        (first audio byte from agent)
t=4,120    agent_speech_end
t=4,610    caller_speech_start       (we start speaking — we know exactly when)
t=6,880    caller_speech_end
t=7,540    agent_speech_start
...
```

Because Convox drives the caller, `caller_speech_start` and `caller_speech_end` are *commanded*, not detected. This removes the VAD error that makes latency measurement fuzzy in transcript-based tools.

### 1.2 The four transcripts

| Name | Source | Used for |
|---|---|---|
| **Ground truth** | The exact text sent to caller TTS, logged pre-synthesis | The reference for all ASR scoring |
| **Agent-heard** | The agent platform's own transcript of the caller, where exposed | STT fault attribution |
| **Convox transcript** | Our independent STT pass over the agent's audio | Scoring agent speech when the platform exposes nothing |
| **Agent-stated** | The platform's transcript of its own output, where exposed | Cross-check against our transcription |

---

## 2. Latency metrics

### `latency.first_response_ms`
Call connect → first agent audio byte. Measures greeting latency, which callers perceive as "is this thing working."

```
first_response_ms = t(first agent audio byte) − t(call_connected)
```

### `latency.response_ms`
The headline metric. Per agent turn:

```
response_ms[i] = t(agent first audio byte, turn i) − t(caller_speech_end, turn i−1)
```

Reported as `p50`, `p90`, `p95`, `max`, `mean`. Turns where the caller barged in are excluded (they're measured by barge-in metrics instead).

> Note this includes the agent's endpointing delay — which is correct, because that is what the caller experiences. Tools that measure from "end of transcription" flatter the agent by hiding its VAD tuning.

### `latency.tool_response_ms`
Same formula, restricted to turns where a tool call occurred. Reported separately because tool turns are structurally slower; a single budget either fails every tool turn or lets slow non-tool turns hide.

### `latency.tts_ttfb_ms`
Where the platform exposes internal spans: agent LLM completion start → first synthesized audio byte. Isolates TTS provider latency from LLM latency.

### `dead_air.max_ms` / `dead_air.count`
Silence exceeding a threshold (default 1,500 ms) where neither party speaks and the call is still open. Excludes deliberate `wait_silence` steps.

### `latency.stability`
Coefficient of variation of `response_ms` across turns. High mean latency is bad; *unpredictable* latency is worse, because callers start talking over the agent.

---

## 3. Turn-taking metrics

The hardest part of voice agents and the least measured.

### `barge_in.stop_ms`
```
stop_ms = t(agent audio stops) − t(caller_speech_start during agent speech)
```
Target: < 300 ms. Above ~500 ms callers repeat themselves, which cascades into duplicate input.

### `barge_in.overrun_bytes` / `barge_in.overrun_ms`
Audio the agent emitted *after* the caller began speaking. Distinct from `stop_ms` because some platforms stop generating but keep flushing a buffer.

### `barge_in.handled`
Boolean: after the interruption, did the agent respond to the interrupting utterance, or resume the sentence it was already saying? Determined by comparing the agent's next turn against the caller's interrupting text (deterministic overlap check, judge fallback for ambiguity).

### `endpoint.false_interrupt_count`
Times the agent started speaking while the caller was mid-utterance (caller speech had not ended and no pause exceeded the agent's configured endpointing threshold). This is the agent interrupting the *caller* — the most irritating failure mode and completely invisible in a transcript.

### `endpoint.delay_ms`
Caller speech end → agent speech start, on turns with no tool call. Approximates the agent's endpointing threshold. Useful as a *diagnostic*: if `response_ms` is high but `endpoint.delay_ms` accounts for most of it, the fix is VAD tuning, not a faster model.

### `backchannel.false_positive_count`
Times a caller backchannel ("mm-hm", "haan") caused the agent to stop or yield turn. Requires a persona with backchanneling enabled.

### `double_talk_ms`
Total duration both parties spoke simultaneously.

### `turn_transition_gap_ms`
Distribution of silence between turns in both directions. Natural human conversation sits around 200 ms; agents that sit at 1,200 ms feel sluggish even when "fast enough."

---

## 4. Speech recognition metrics

This is where the ground-truth architecture pays off.

### `asr.wer`
Word error rate of the agent's understanding, computed against known ground truth:

```
WER = (S + D + I) / N
```
over the aligned ground-truth text and the agent-heard transcript, after language-appropriate normalization.

When the platform doesn't expose its transcript, Convox computes a **proxy WER** by running its own STT over the caller audio *as degraded by the channel simulator*, and marks the metric `proxy: true`. That measures channel damage, not the agent's ASR — and it says so rather than pretending otherwise.

### `asr.cer`
Character error rate. The meaningful metric for Indic scripts, where word segmentation conventions make WER unstable.

### `asr.entity_error_rate`
WER restricted to entity tokens — names, numbers, medications, dates. A 5% overall WER that puts all its errors in the phone number is a broken agent; a 15% WER that only misses filler words is fine. This metric separates them.

### `asr.slot_accuracy`
Fraction of `caller.facts` values the agent captured correctly (per `slot.captured` normalization). The most business-relevant ASR number: did it get the data right?

### Normalization (per language)

| Family | Normalization applied |
|---|---|
| English | Lowercase, punctuation strip, number-word ↔ digit unification, contraction expansion, filler removal (`um`, `uh`) |
| Indic (Devanagari, Tamil, Telugu, Bengali, Kannada, Malayalam, Gujarati, Odia, Punjabi, Assamese) | Unicode NFC, matra and nukta normalization, ZWJ/ZWNJ removal, numeral unification (`२०००` / `2000` / `do hazaar`), honorific tolerance, common transliteration equivalence |
| Code-switched | Per-token language ID, then per-token normalization; Roman↔native transliteration treated as equivalent |

**Why this matters:** naive WER on Devanagari reports errors for `नमस्ते` vs `नमस्‍ते` (a ZWJ difference) and treats `2000` vs `दो हज़ार` as a total miss. Both make correct agents look broken, which is exactly the kind of noise that makes teams stop trusting an eval tool.

---

## 5. Audio quality metrics

### `audio.snr_db`
Estimated speech-to-noise ratio on the agent leg. Low SNR on the *agent's* output usually indicates a TTS or transport problem.

### `audio.clipping_ratio`
Fraction of samples at or above full scale.

### `audio.truncation_detected`
Boolean. The agent's final utterance ends without a natural energy decay — detected via an abrupt cutoff at high energy plus an incomplete transcript tail. This is a very common bug (call teardown races the last TTS chunk) and is invisible in transcripts, since the text was generated in full even though the audio never played.

### `audio.artifact_score`
Detects garbled TTS via spectral discontinuity: frame-to-frame spectral distance spikes, unnatural pitch jumps, and zero-crossing anomalies. Scored 0–1; anything above 0.3 warrants listening.

### `audio.silence_ratio`
Fraction of the agent leg that is silence — surfaces both dead air and one-way audio faults.

### `audio.loudness_lufs`
Integrated loudness. Consistency matters across turns; a jump usually means a voice or provider fallback switched mid-call.

---

## 6. Conversation quality metrics

### `talk_ratio`
Agent speech duration ÷ total speech duration. Well-behaved task agents typically land 0.45–0.65. Above 0.8 means the agent is monologuing.

### `turn_count` / `turns_to_resolution`
Total turns, and turns until the goal was met. The efficiency metric leadership actually cares about.

### `repetition_score`
Longest n-gram repeated across agent turns, and repeat count. Catches loop bugs ("I didn't catch that. I didn't catch that.") that pass every other check.

### `sentiment_trajectory`
Caller sentiment per turn, and the delta from first to last. A call that ends more negative than it started is worth listening to regardless of whether it "passed."

### `politeness` / `tone_consistency`
Judged metrics, run only when a scenario asks for them.

### `instruction_following_score`
Judged 0–1 against the agent's own system prompt, supplied as context. Correlates strongly with what humans mean by "the agent went off the rails."

---

## 7. Task metrics

### `goal_achieved`
Did the caller's goal get met? Deterministic when the scenario defines a checkable outcome (a tool call with the right arguments); judged otherwise. Convox prefers the deterministic form and warns when a scenario only offers the judged one.

### `tool_call_accuracy`
```
accuracy = correct_calls / (correct + missing + spurious + wrong_args)
```

### `script_adherence`
Fraction of required steps (disclosure, verification, confirmation) the agent performed, per the scenario's declared checklist.

### `containment`
Resolved without transfer or escalation. The metric contact-center buyers actually budget against.

### `data_capture_accuracy`
Same as `asr.slot_accuracy` but rolled up per run and per field, so "we lose 12% of phone numbers" becomes visible across scenarios.

---

## 8. Reliability metrics

### `pass^k`
```
pass^k(scenario) = passed_repeats / total_repeats
```
The headline verdict. A scenario that passes 3/5 is not a pass; it is a 60% agent.

### `flake_rate`
Fraction of scenarios in a run with `0 < pass^k < 1`.

### `variance_source`
For flaky scenarios, attributes variance to:
- **agent** — same caller input, different agent behavior
- **caller** — the agentic caller LLM behaved differently
- **judge** — identical trial, different judge verdict (measured via vote disagreement)

Three very different bugs; only the first is the agent's fault. Reporting them together is how eval tools lose credibility.

### `measurement_noise`
Run-over-run variance on unchanged scenario/target pairs. Convox's self-check on its own reliability.

---

## 9. Cost metrics

### `cost.test_usd`
Per trial: caller STT + caller LLM + caller TTS + judge tokens + telephony. Broken out per component.

### `cost.target_estimate_usd`
Estimated cost the agent itself incurred, using configured per-minute/per-token rates for its platform.

### `cost.per_scenario` / `cost.per_run`
Rollups, with the budget guard's remaining headroom.

### `cost.per_bug_found`
Run cost ÷ new distinct failures found. The number that justifies the tool in a budget review, and the one that tells you when your suite has gone stale (cost climbing, discoveries flat).

---

## 10. Production-only metrics

Computed on ingested calls where simulation-side ground truth doesn't exist:

| Metric | Notes |
|---|---|
| `production.task_success_rate` | Judged, sampled, calibrated against human labels |
| `production.containment_rate` | Deterministic from transfer events |
| `production.abandon_rate` | Caller hung up before resolution |
| `production.latency.*` | Same formulas, computed from platform traces or recording analysis |
| `production.drift.*` | Statistical change detection (CUSUM / Page-Hinkley) on any metric |
| `production.cohort.*` | Any metric sliced by language, region, hour, agent version |

**Test-vs-production comparison** is a first-class view: the same metric, same definition, both populations, with the gap highlighted. A 20-point drop from test to production means the test suite doesn't represent reality — which is itself the most valuable finding a tool like this can produce.

---

## 11. Default thresholds

Shipped as a starting point, meant to be overridden per project:

| Metric | Good | Warn | Fail |
|---|---|---|---|
| `latency.first_response_ms` | < 800 | 800–1,500 | > 1,500 |
| `latency.response_ms.p95` | < 1,200 | 1,200–2,000 | > 2,000 |
| `latency.tool_response_ms.p95` | < 2,500 | 2,500–4,000 | > 4,000 |
| `barge_in.stop_ms.max` | < 300 | 300–600 | > 600 |
| `endpoint.false_interrupt_count` | 0 | 1 | > 1 |
| `dead_air.max_ms` | < 1,500 | 1,500–3,000 | > 3,000 |
| `asr.entity_error_rate` | < 0.02 | 0.02–0.05 | > 0.05 |
| `asr.slot_accuracy` | > 0.98 | 0.95–0.98 | < 0.95 |
| `audio.truncation_detected` | false | — | true |
| `repetition_score` | < 2 | 2–3 | > 3 |
| `talk_ratio` | 0.45–0.65 | 0.65–0.8 | > 0.8 |
| `pass^k` | 1.0 | 0.8–1.0 | < 0.8 |

---

## 12. Methodology notes

- **All latency is caller-perceived.** We measure from the caller's ear, not from internal spans, because that's what determines whether a human talks over the agent. Internal spans are reported additionally, never instead.
- **Percentiles need samples.** `p95` over 4 turns is meaningless; Convox marks percentile metrics `low_confidence` below 20 samples and aggregates across a scenario's repeats where valid.
- **Every metric is reproducible.** Given the artifact bundle, `convox report --rescore` recomputes every number without re-calling the agent. Metric definitions are versioned, and a run records the metric-definition version it was scored with.
- **We publish the formulas.** A benchmark whose scoring is a black box is marketing. This document is the reason our numbers can be argued with — which is the only reason they're worth anything.
