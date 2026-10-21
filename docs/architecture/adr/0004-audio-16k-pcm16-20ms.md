# ADR 0004: Internal audio format: 16 kHz, PCM16 mono, 20 ms frames (640 bytes)

This ADR fixes the audio format used everywhere inside the server, and where resampling is allowed.

## Status

Accepted (October 2026).

## Context

- Sarvam STT wants 16 kHz input.
- PSTN audio to the ear is 8 kHz codec audio; that is unavoidable on the wire.
- Silero VAD's hop matches 20 ms frames (it uses 512-sample windows at 16k, about 32 ms; feed 32 ms windows with a 20 ms hop).
- Exotel's voicebot applet sends PCM16 mono base64. The overview says 8 kHz by default or 16 kHz on request; the spec calls Exotel's docs ambiguous on this (open question Q1). Plivo offers `audio/x-l16;rate=16000` (if accepted) or `audio/x-mulaw;rate=8000`.

## Decision

- **Internally: 16 kHz, PCM16 mono, 20 ms frames = 640 bytes.** Every component above the transport sees only this.
- **Exotel** at `sample-rate=16000`. Confirm from the `start` message's `media_format`; if 8k arrives anyway, resample both directions and log it.
- **Plivo** at 16k if available, else mulaw 8k, decoded and resampled 8k ⇄ 16k with **soxr**.
- Resampling happens only at the transport edge.
- Outbound to providers is chunked by the transport (Exotel: 100 ms = 3,200 bytes at 16k, multiple of 320); the mixer writes 20 ms frames.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Run the pipeline at 8 kHz | STT gets worse input; Sarvam wants 16k. |
| Mixed rates per component | Complicates the mixer and VAD; one format keeps the mixer simple. |
| Larger frames (e.g. 100 ms internally) | Does not match Silero's hop; makes VAD/EOU and barge-in coarser. |

## Consequences

- If Exotel ignores `sample-rate=16000`, a soxr resampler sits on the hot path in both directions. This is the first thing tested with the first sponsor minute ([open-questions.md](../../plan/open-questions.md)).
- Unit tests cover resampler round-trip and the frame clock ([testing-strategy.md](../../engineering/testing-strategy.md)).
- The `legs.sample_rate` column records what each leg actually delivered.

## Related

- [audio.md](../../tech-spec/audio.md)
- [leg-transports.md](../../tech-spec/leg-transports.md)
- [telephony-integration.md](../telephony-integration.md)
- [ADR 0005](0005-vad-silero-plus-sarvam-eou.md)
