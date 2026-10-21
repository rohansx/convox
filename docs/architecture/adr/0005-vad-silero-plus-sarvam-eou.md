# ADR 0005: VAD and endpointing: Silero VAD v5 per leg + Sarvam streaming end-of-utterance

This ADR records how dubhash decides a speaker has finished (end of utterance, EOU), which is the single biggest latency and quality lever in the product.

## Status

Accepted (October 2026).

## Context

- First translated word heard = EOU wait + about 1.2 s (STT final + MT + TTS first byte + playout). With EOU starting at 550 ms that is about 1.4 to 1.8 s. Targets: p50 under 1.5 s, p95 under 2.0 s, on real PSTN.
- EOU too short clips sentences mid-way (false EOU); too long feels dead.
- Speakers of different languages pause differently (Tamil vs Hindi).
- Input is 8 kHz Indic telephony audio upsampled to 16k.

## Decision

- **Silero VAD v5 (onnx) per leg**, on every 20 ms frame (512-sample window at 16k, about 32 ms; hop 20 ms). Speech prob > 0.5 = speech. Also computes RMS for ducking and backchannel duration.
- **Plus Sarvam's streaming end-of-utterance / final signal when available**: if it arrives earlier, take it.
- EOU fires when **silence ≥ `eou_ms`** (per pair in `pairs.yaml`, **start at 550 ms**) **and utterance ≥ 300 ms**.
- Utterances **< 600 ms with ≤ 2 words** are tagged `backchannel` and not translated (`backchannel_max_ms: 600`).
- Tune `eou_ms` per pair by measuring false-EOU rate vs latency on the bench set.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Turn-detector models (LiveKit EOU, Smart Turn) | English-tuned; break on 8k Indic audio. Revisit when one works in Hindi. |
| Silence threshold only, fixed across languages | Ignores per-language pause patterns; 550 ms is a starting guess, not an answer. |

## Consequences

- Cheap, CPU-only, well understood.
- Whether Sarvam's streaming STT emits a usable EOU / final-on-silence event decides the EOU latency floor: TBD — see [open-questions.md](../../plan/open-questions.md).
- `t_eou` (ms after last speech frame) is logged per turn; false-EOU rate is a tuning metric ([evaluation-and-metrics.md](../../engineering/evaluation-and-metrics.md)).

## Related

- [interpreter-pipeline.md](../../tech-spec/interpreter-pipeline.md)
- [configuration.md](../../tech-spec/configuration.md)
- [latency-engineering.md](../../engineering/latency-engineering.md)
- [ADR 0006](0006-stt-sarvam-deepgram.md)
