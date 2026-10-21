# ADR 0007: Translation: Sarvam translate (Mayura), sentence-level; LLM repair is v2

This ADR records the machine-translation provider and its call pattern.

## Status

Accepted (October 2026).

## Context

- Language pairs for the buildathon: hi ⇄ kn, hi ⇄ ta, mr ⇄ hi. Direct Indic → Indic is required.
- MT is on the serial critical path (about 200 ms in the budget).
- Greetings, confirmations and numbers repeat across turns.
- Numbers, addresses, names and times must survive (entity accuracy target over 95%).

## Decision

- **Sarvam translate (Mayura), sentence-level.** One request per utterance. Example: `source=hi-IN target=kn-IN`, mode colloquial, with `numerals_format=native` off (keep digits).
- **Exact-match translation cache per pair** skips MT entirely (about 200 ms) for repeated text (v1).
- **An LLM only for low-confidence repair, in v2.** Not in v1.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Google Translate | Indic pair quality, and not a sponsor. |
| LLM translation on every turn | Latency on the critical path; kept as a v2 repair step for low-confidence sentences. |

## Consequences

- Behind the MT interface with `FakeMT`.
- `t_mt` per turn and meaning rating (kept / partly / lost) on 300 turns per pair before the number goes in the deck ([evaluation-and-metrics.md](../../engineering/evaluation-and-metrics.md)).
- Short sentences translate faster and better; the opening line asks speakers for short sentences.

## Related

- [interpreter-pipeline.md](../../tech-spec/interpreter-pipeline.md)
- [ADR 0006](0006-stt-sarvam-deepgram.md)
- [ADR 0008](0008-tts-bulbul-indic-eleven-hi-en.md)
