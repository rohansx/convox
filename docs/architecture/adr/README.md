# Architecture decision records

This folder holds one record per significant architecture decision for dubhash: what was decided, why, and what lost. They come from the tech spec's "decisions and why" table plus decisions made where the product overview and tech spec disagreed. Read them before proposing a change to the stack; to change a decision, add a new ADR that supersedes the old one rather than editing history.

## Index

| # | Decision | Status |
|---|---|---|
| [0001](0001-python-asyncio.md) | Python 3.12 + asyncio; hot path in numpy/onnx; Rust only if profiling says so | Accepted |
| [0002](0002-own-topology-not-pipecat.md) | Own the topology (FastAPI + websockets); borrow Pipecat classes, not its pipeline | Accepted |
| [0003](0003-telephony-exotel-primary-plivo-fallback.md) | Exotel voicebot applet primary, Plivo fallback, behind `LegTransport`; WebRTC for dev | Accepted |
| [0004](0004-audio-16k-pcm16-20ms.md) | 16 kHz PCM16 mono, 20 ms (640-byte) frames internally; soxr at the edge | Accepted |
| [0005](0005-vad-silero-plus-sarvam-eou.md) | Silero VAD v5 + Sarvam EOU; `eou_ms` per pair, start 550 ms | Accepted |
| [0006](0006-stt-sarvam-deepgram.md) | Sarvam Saarika/Saaras streaming STT; Deepgram Nova for English/Hinglish | Accepted |
| [0007](0007-translation-sarvam-mayura.md) | Sarvam Mayura sentence-level translation; LLM repair is v2 | Accepted |
| [0008](0008-tts-bulbul-indic-eleven-hi-en.md) | Bulbul for Indic targets, ElevenLabs Flash v2.5 for hi/en, per language in config | Accepted |
| [0009](0009-in-process-call-state.md) | In-process state per call; Redis registry only; Postgres off the hot path | Accepted |
| [0010](0010-single-vm-mumbai.md) | One VM in ap-south-1, Docker Compose (app, Postgres, Redis, Caddy) | Accepted |
| [0011](0011-console-ssr-sse.md) | Console as server-rendered HTML + SSE (htmx), no SPA | Accepted |
| [0012](0012-no-voice-cloning-v1.md) | No voice cloning in v1; gender-matched natural voice | Accepted |

## Template

```markdown
# ADR NNNN: <title>

<one-paragraph purpose>

## Status
Proposed | Accepted | Superseded by NNNN

## Context
## Decision
## Alternatives considered
| Alternative | Why it lost |
## Consequences
## Related
```

## Related

- [system-overview.md](../system-overview.md)
- [telephony-integration.md](../telephony-integration.md)
- [open-questions.md](../../plan/open-questions.md)
- [conventions.md](../../engineering/conventions.md)
