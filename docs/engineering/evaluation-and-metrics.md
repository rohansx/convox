# Evaluation and metrics

This document defines how we prove Convox works: the v1 metrics with exact definitions and targets, the eval corpus and rating process, what we publish, and the limits we state openly. It is the contract between the build (P2's bench, Rohan's controller) and the claims we make in the deck and to platforms. P5 owns the corpus, the raters and the published numbers.

## v1 metrics

| Metric | Definition | Target (v1) | Source of truth | How measured |
|---|---|---|---|---|
| **first-audio latency** | speaker stops → listener hears first translated word, on real PSTN legs | p50 < 1.5 s, p95 < 2.0 s | `turns` on PSTN calls | see [latency engineering](latency-engineering.md) |
| **meaning preservation** | a bilingual human rates each turn: meaning kept / partly / lost, on 300 real turns per pair | > 90% "kept" on hi⇄kn, hi⇄ta, mr⇄hi | `tools/label.py` ratings | rater pass over turn logs |
| **entity accuracy** | numbers, addresses, names and times survive the round trip | > 95% | `tools/label.py` entity checks | rater marks each entity in src as preserved / not in tgt |
| **dropped utterances** | things said that were never played to the other side | **0** (a drop is a bug) | `turns` + `events` | every non-backchannel utterance must have a turn with `t_play_end` set or `clipped_ms` set |
| **false clears** | translations cut by a backchannel that should not have interrupted | < 3% of turns | `turns.clipped_ms` + `events(kind=barge_in)` | clipped turns where the barge-in speech was < `barge_in_min_ms` or was a backchannel, reviewed by hand |
| **task completion** | scripted scenarios (find the building, confirm a dose, agree a time) completed, compared with a plain bridge between speakers who share no language | > 85% vs < 40% baseline | PSTN scenario runs | see [testing strategy](testing-strategy.md) |
| **escape rate** | calls where a party pressed 0 to turn interpretation off | < 5%, and every one reviewed | `calls.escape_used` | count, then review each call's turn log |

Supporting engineering metrics (not published, used for tuning):

| Metric | Use |
|---|---|
| per-stage p50/p95 (`t_eou`, `t_stt_final`, `t_mt`, `t_tts_ttfb`, `t_tts_done`) per pair | find the slow stage |
| WER against reference transcripts (bench) | STT provider choice per language |
| false-EOU rate (sentence split mid-way) per `eou_ms` | per-pair EOU tuning |
| translation cache hit rate | is the cache worth it |
| gaps per call (`events` kind `gap`) | transport health |
| clipping flag rate (mixer) | mixer correctness; should be 0 |

## Eval corpus

| Item | Spec |
|---|---|
| Utterances | **30 per language**, recorded by the team |
| Quality | phone quality (8 kHz-equivalent), with background noise variants |
| Languages | those in the three rehearsed pairs: hi, kn, ta, mr |
| References | a reference transcript per utterance, for WER |
| Content | scenario-shaped speech: directions, building/floor, doses, times, numbers, confirmations, plus backchannels ("haan", "hmm", "sari", "ok") to exercise the backchannel rule |
| Storage | `tests/fixtures/<lang>/*.wav` + transcript file; if too large for git, a bucket (TBD, see [open questions](../plan/open-questions.md)) |
| Consent | everyone recorded agrees in writing that the recording can be used for eval; no real customer audio |

The bench corpus measures latency and WER. Meaning numbers come from **300 rated turns per pair**, drawn from bench runs and live calls (PSTN where possible). The spec and overview don't say how the 300 are split between bench and live. Proposed: report the split alongside the number.

## Rating process (`tools/label.py`)

1. A bilingual rater for the pair (native in at least one side) sees src text and tgt text, plus audio if available.
2. Rates meaning: **kept** (a listener would act correctly), **partly** (gist survives, a detail is wrong or missing), **lost** (wrong or misleading).
3. Marks each entity (number, address, name, time) as preserved or not.
4. Free-text note for mistranslations; these feed the phrase list and the translation cache.

Rules:

- 300 turns per pair before a number goes in the deck as final. If a pair has fewer by CP2, its number goes in labelled "preliminary, n = …" or is left out (R-EVAL in [risks](../plan/risks.md), [10-day plan](../plan/buildathon-10-day-plan.md#cut-list-in-order)).
- Where possible, use one rater per pair for consistency. A second rater on a sample is ideal; whether we have time for it is TBD.
- Ratings are stored with the turn id so each number can be traced back to its turns.

## What we publish

- Per pair (hi⇄kn, hi⇄ta, mr⇄hi): first-audio p50/p95 on PSTN, % kept / partly / lost (n = 300, or labelled preliminary with the actual n), entity accuracy, drop count, false-clear rate.
- How each was measured: PSTN vs bench, n, rater count, date.
- Only pairs we have measured. We do not claim any pair or language whose STT we have not measured on PSTN.

## Honest limits

These go into the deck and README word for word in substance:

- **Low-resource languages** (Santali, Bhojpuri, Tulu, the Northeast) are not supported in v1, because the engines aren't good enough. We say which pairs we trust and publish numbers per pair.
- **8 kHz telephony audio with background noise degrades STT.** We publish results on real PSTN calls, not browser audio.
- **Idioms, sarcasm and anger do not translate cleanly.** The ducked original carries tone; the words may not.
- **A 1.5 s pause changes the rhythm of a conversation.** Some people will hate it, and the DTMF 0 escape hatch exists for them.
- **Voice:** v1 uses a gender-matched natural voice per target language, chosen by a pitch heuristic. It does not sound like the speaker. Voice cloning is v2 and needs explicit consent.
- **TTS cost line is an estimate.** Bulbul pricing must be re-checked. See [business model](../product/business-model.md).

## Related

- [Testing strategy](testing-strategy.md)
- [Latency engineering](latency-engineering.md)
- [Data model](../tech-spec/data-model.md)
- [Demo script](../plan/demo-script.md)
- [Risks](../plan/risks.md)
- [Product overview](../product/overview.md)
- [Open questions](../plan/open-questions.md)
