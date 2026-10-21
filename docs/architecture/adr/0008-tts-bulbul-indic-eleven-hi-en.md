# ADR 0008: TTS: Sarvam Bulbul for Indic targets, ElevenLabs Flash v2.5 for Hindi and English, chosen per language

This ADR records the text-to-speech providers and how a provider and voice are chosen for each target language. It resolves a disagreement between the product overview (ElevenLabs for all TTS) and the tech spec; the tech spec wins.

## Status

Accepted (October 2026). Supersedes the product overview's "ElevenLabs flash for all TTS" assumption.

## Context

- ElevenLabs Flash's language list does not cover every scheduled language natively; Bulbul does, with roughly similar first-byte latency.
- ElevenLabs is not hosted in India: 150 to 250 ms RTT from Mumbai. Sarvam is in India ([ADR 0010](0010-single-vm-mumbai.md)).
- TTS first byte is on the serial critical path (about 300 ms in the budget).
- Voice cloning is a v2 feature behind explicit consent ([ADR 0012](0012-no-voice-cloning-v1.md)).

## Decision

- **Sarvam Bulbul is primary for Indic target languages:** kn, ta, mr, te, ml, bn, gu, pa, od.
- **ElevenLabs Flash v2.5 for Hindi and English targets**, and for voice cloning in v2.
- **One TTS interface**, provider chosen **per target language** in `config/pairs.yaml` (`tts: bulbul | eleven`, `voice_m`, `voice_f`). This is a config table, not an architecture decision.
- Streaming per utterance over a warm websocket (one per leg-direction); the first chunk is forwarded to the listener leg's `send()` as soon as it arrives.

```yaml
# config/pairs.yaml (excerpt)
hi-kn:
  a: { lang: hi-IN, stt: sarvam, tts: eleven, voice_m: "eleven:hi-male-1",  voice_f: "eleven:hi-female-1" }
  b: { lang: kn-IN, stt: sarvam, tts: bulbul, voice_m: "bulbul:kn-male-1", voice_f: "bulbul:kn-female-1" }
```

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| ElevenLabs Flash for every target (product overview) | Not every scheduled language covered natively; non-India RTT of 150 to 250 ms on every Indic turn. |
| Bulbul for every target | Hindi and English voices and v2 cloning favour ElevenLabs; kept per-language so either can be swapped. |

## Consequences

- Verify each target language on both providers: for each of kn, ta, mr, a 30-minute listening test on naturalness and first-byte from Mumbai; the result goes in `pairs.yaml` ([open-questions.md](../../plan/open-questions.md)).
- **Cost:** the overview's unit economics assumed ElevenLabs for all TTS (₹1.5 to 2.5 per minute). With Bulbul on Indic targets the TTS cost line is an **estimate**; Bulbul pricing must be re-checked. See [business-model.md](../../product/business-model.md).
- `t_tts_ttfb`, `tts_provider` and `voice` are logged per turn. `t_tts_done` is measured by the bench but has no `turns` column (see [latency-engineering.md](../../engineering/latency-engineering.md#timestamps-to-log)).

## Related

- [interpreter-pipeline.md](../../tech-spec/interpreter-pipeline.md)
- [configuration.md](../../tech-spec/configuration.md)
- [ADR 0012](0012-no-voice-cloning-v1.md)
- [latency-engineering.md](../../engineering/latency-engineering.md)

## Amendment (21 Oct 2026): Bulbul for Hindi too

**Status:** Accepted (Rohan). Bulbul now speaks every target language, Hindi included. ElevenLabs stays wired (`tts: eleven`) and can be selected per pair, but is off the default path.

- The ElevenLabs account is on the free tier, which cannot use library voices over the API (HTTP 402 `paid_plan_required`).
- Bulbul Hindi measured live: first byte 214–386 ms, similar to or better than Flash's ~300 ms. Sarvam is India-hosted, which fits the data-residency claim, and it means one vendor fewer.
- Voice cloning (v2) would still need ElevenLabs or another cloning vendor; see [ADR 0012](0012-no-voice-cloning-v1.md).

The same day, `hi-kn` switched its Hindi side to the speech→English pivot (`stt: sarvam-translate`, `mt_src: en-IN`), ~670 ms faster at p50. The direct route is `hi-kn-direct`. See [open questions](../../plan/open-questions.md), L1.
