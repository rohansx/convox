# ADR 0006: STT: Sarvam streaming (Saarika/Saaras) for Indic, Deepgram Nova for English and Hinglish fallback

This ADR records the speech-to-text providers and how they are selected.

## Status

Accepted (October 2026).

## Context

- Both Sarvam and Deepgram are buildathon sponsors.
- Indic STT on telephony audio crossed the usability line in 2026 for the major languages.
- Only the STT final is on the critical path if STT streams during speech (about 300 ms after EOU in the budget).
- Sarvam websocket connect + auth is 150 to 300 ms, so per-utterance reconnects are expensive.

## Decision

- **Sarvam streaming STT (Saarika/Saaras websocket) for Indic.** Language code from pair config (`hi-IN`, `kn-IN`, ...).
- **Deepgram Nova streaming for English and Hinglish fallback.**
- **Selectable per leg** in `pairs.yaml` (`stt: sarvam | deepgram`).
- Frames forwarded live; partials kept for the console; on EOU send end-of-stream and await the final.
- Keep one warm STT socket per leg and send a reset between utterances instead of reconnecting (v1).

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Whisper | Latency, and hallucination on silence. |

## Consequences

- Behind the STT interface with `FakeSTT` for tests.
- Whether a Sarvam stream can stay open across utterances with a reset, or must reconnect (a reported Pipecat issue says SarvamSTT dies after 60 s of silence, suggesting a keepalive), is open: TBD — see [open-questions.md](../../plan/open-questions.md).
- `t_stt_final` and WER on the bench set are measured per pair. No language ships whose STT we have not measured on PSTN.

## Related

- [interpreter-pipeline.md](../../tech-spec/interpreter-pipeline.md)
- [configuration.md](../../tech-spec/configuration.md)
- [evaluation-and-metrics.md](../../engineering/evaluation-and-metrics.md)
- [ADR 0005](0005-vad-silero-plus-sarvam-eou.md)
- [ADR 0007](0007-translation-sarvam-mayura.md)
