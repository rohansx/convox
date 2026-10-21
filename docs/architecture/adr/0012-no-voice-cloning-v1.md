# ADR 0012: No voice cloning in v1: a gender-matched natural voice per target language

This ADR records what voice the listener hears in v1. It resolves the product overview's tagline ("a voice that sounds like the person speaking") against the spec: v1 does not clone.

## Status

Accepted (October 2026).

## Context

- Cloning a speaker's voice without asking is a DPDP problem and a trust problem.
- The ducked original (speaker's real voice at −14 dB under the translation) already carries most of the "this is a real person" signal.
- Voice cloning from the first 30 seconds of a speaker is planned for v2, behind explicit consent at call start.

## Decision

- **v1 uses a fixed natural voice per target language and detected speaker gender** (`voice_m` / `voice_f` in `pairs.yaml`). A male Hindi speaker is heard in Kannada as a male voice.
- Gender is detected by a **pitch heuristic** on the first 2 s of the speaker.
- Product copy and demos say **"a gender-matched natural voice"**. They never claim v1 sounds like the speaker.
- **Voice cloning is v2**, needs explicit consent, and uses ElevenLabs ([ADR 0008](0008-tts-bulbul-indic-eleven-hi-en.md)).

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Clone from the first 30 s (v1) | No consent flow in v1; DPDP and trust risk; non-goal for v1. |
| One voice per language regardless of gender | Breaks the "who is speaking" cue more than a matched voice. |
| Platform passes gender in the API / consumer flow asks | Possible alternative to the pitch heuristic; open. |

## Consequences

- The pitch heuristic on the first 2 s is crude; whether it is acceptable for v1, or the platform should pass gender, is TBD — see [open-questions.md](../../plan/open-questions.md).
- The `voice` used is logged on every turn.
- The pitch-based gender detector is proposed for P2, consumed by P3 for voice selection; ownership is open as PQ20 ([roles-and-ownership.md](../../plan/roles-and-ownership.md), [open-questions.md](../../plan/open-questions.md)).

## Related

- [interpreter-pipeline.md](../../tech-spec/interpreter-pipeline.md)
- [security-privacy.md](../../tech-spec/security-privacy.md)
- [ADR 0008](0008-tts-bulbul-indic-eleven-hi-en.md)
- [overview.md](../../product/overview.md)
