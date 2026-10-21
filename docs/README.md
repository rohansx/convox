# dubhash docs

This folder is the documentation for dubhash: product, architecture, technical spec, plan and engineering practice. It is written for the five engineers building v1 for the Bolna Symphony Buildathon (build days Wed 21 to Fri 30 Oct 2026, submission Sat 31 Oct 2026, finale 20 Nov 2026 in Bengaluru). Where the product overview and the tech spec disagreed, the docs follow a fixed set of resolved decisions. What is still unknown is collected in [open questions](plan/open-questions.md).

## What dubhash is

dubhash is a live interpreter that sits inside a phone call between two people who speak different Indian languages. A CPaaS (Exotel primary, Plivo fallback) places two calls, one to each person, each streaming two-way audio to our server. dubhash joins them itself, so A hears B translated and B hears A translated, about 1.4 to 1.8 s after each one stops speaking. The listener hears a gender-matched natural voice with the original speaker ducked underneath. The product is the bridge, the floor controller that manages turn-taking, and the integrations: a one-line platform API, ward mode on a single phone, and a dial-in number.

## Start here

If you are new, read in this order. It takes about an hour.

1. [Product overview](product/overview.md): what we are building, what it is not, and what goes on stage.
2. [System overview](architecture/system-overview.md): components, one process per call, hot-path rules, and the request flow from `POST /v1/calls` to the first translated word.
3. [Telephony integration](architecture/telephony-integration.md): how a server gets inside a phone call (two voicebot legs).
4. [Floor controller](tech-spec/floor-controller.md): states, rules and invariants. This is the core of the product.
5. [Interpreter pipeline](tech-spec/interpreter-pipeline.md): VAD → EOU → STT → MT → TTS, with the latency budget.
6. [Buildathon 10-day plan](plan/buildathon-10-day-plan.md) and [roles and ownership](plan/roles-and-ownership.md): who does what on which day.
7. [Repo structure](engineering/repo-structure.md), [dev setup](engineering/dev-setup.md) and [conventions](engineering/conventions.md): before your first PR.
8. [Open questions](plan/open-questions.md): what is still undecided and who owns each item.

Then read the spec for your own module (see [roles](plan/roles-and-ownership.md)).

## Resolved decisions (apply everywhere)

| # | Topic | Decision |
|---|---|---|
| 1 | TTS | Sarvam Bulbul for Indic targets (kn, ta, mr, te, ml, bn, gu, pa, od). ElevenLabs Flash v2.5 for Hindi and English targets, and for cloning in v2. Chosen per language in `config/pairs.yaml`. The TTS cost line is an estimate until Bulbul pricing is re-checked. |
| 2 | Voice | v1 uses a fixed natural voice per target language and detected speaker gender (pitch heuristic). Never claim it sounds like the speaker. Cloning is v2 and needs explicit consent. |
| 3 | Schedule | 10 build days: Day 1 = Wed 21 Oct, Day 10 = Fri 30 Oct, submit Sat 31 Oct. CP1 = two real phones, both directions, no drops. CP2 = feature freeze. |
| 4 | Floor states | `IDLE`, `A_SPEAKING`, `B_SPEAKING`, `PLAYING_AB`, `PLAYING_BA`. "Queued" is a condition, not a state. |
| 5 | Latency | First word heard = EOU wait (start 550 ms) + about 1.2 s, so about 1.4 to 1.8 s. Targets p50 < 1.5 s, p95 < 2.0 s, on real PSTN. |
| 6 | Team | Rohan: architecture, session, floor controller, mixer, demo and pitch. P2: audio/ML. P3: backend, legs, TTS. P4: console and ward mode. P5: product and eval. |
| 7 | Stack | Python 3.12 asyncio, FastAPI + websockets (Pipecat classes as libraries only), numpy/onnx, soxr, Silero VAD v5, Sarvam STT + Deepgram Nova fallback, Sarvam Mayura, Postgres + Redis, Docker Compose + Caddy on one ap-south-1 VM, console as server-rendered HTML + SSE (htmx). |

Decision 3's original weekday labels (Tue/Thu) were wrong: 21 Oct 2026 is a Wednesday. The dates stand, and Day 4 and Day 5 (CP1) fall on the weekend.

## Index

### Product

| Doc | What it covers |
|---|---|
| [overview.md](product/overview.md) | What dubhash is and is not, why now, the three ways it ships, the buildathon deliverable |
| [problem-and-users.md](product/problem-and-users.md) | The four user groups and their failing calls, two worked example calls |
| [business-model.md](product/business-model.md) | Who pays, unit economics per minute (with the TTS cost caveat), moat, competitors, go-to-market |
| [glossary.md](product/glossary.md) | Shared vocabulary: legs, floor, mark, clear, EOU, backchannel and more |

### Architecture

| Doc | What it covers |
|---|---|
| [system-overview.md](architecture/system-overview.md) | Component map, `CallSession`, hot-path rules, storage, deployment topology, environments |
| [telephony-integration.md](architecture/telephony-integration.md) | Number masking, the two-voicebot-leg trick, Exotel and Plivo protocols, outbound setup, dial-in IVR |
| [adr/README.md](architecture/adr/README.md) | Index and template for architecture decision records |
| [adr/0001](architecture/adr/0001-python-asyncio.md) | Python 3.12 + asyncio; hot path in numpy/onnx; Rust only if profiling says so |
| [adr/0002](architecture/adr/0002-own-topology-not-pipecat.md) | Own the topology; borrow Pipecat classes, not its pipeline |
| [adr/0003](architecture/adr/0003-telephony-exotel-primary-plivo-fallback.md) | Exotel primary, Plivo fallback, behind `LegTransport`; WebRTC for dev |
| [adr/0004](architecture/adr/0004-audio-16k-pcm16-20ms.md) | 16 kHz PCM16 mono, 20 ms (640-byte) frames; soxr at the edge |
| [adr/0005](architecture/adr/0005-vad-silero-plus-sarvam-eou.md) | Silero VAD v5 + Sarvam EOU; `eou_ms` per pair, start 550 ms |
| [adr/0006](architecture/adr/0006-stt-sarvam-deepgram.md) | Sarvam Saarika/Saaras STT; Deepgram Nova for English/Hinglish |
| [adr/0007](architecture/adr/0007-translation-sarvam-mayura.md) | Sarvam Mayura sentence-level translation; LLM repair is v2 |
| [adr/0008](architecture/adr/0008-tts-bulbul-indic-eleven-hi-en.md) | Bulbul for Indic targets, ElevenLabs Flash v2.5 for hi/en |
| [adr/0009](architecture/adr/0009-in-process-call-state.md) | In-process state per call; Redis registry only; Postgres off the hot path |
| [adr/0010](architecture/adr/0010-single-vm-mumbai.md) | One VM in ap-south-1 with Docker Compose |
| [adr/0011](architecture/adr/0011-console-ssr-sse.md) | Console as server-rendered HTML + SSE, no SPA |
| [adr/0012](architecture/adr/0012-no-voice-cloning-v1.md) | No voice cloning in v1; gender-matched natural voice |

### Tech spec

| Doc | What it covers |
|---|---|
| [interpreter-pipeline.md](tech-spec/interpreter-pipeline.md) | One direction's eight stages, latency budget, backchannel rule, translation cache, interfaces |
| [floor-controller.md](tech-spec/floor-controller.md) | States, rules, invariants, the speaking-while-playing case, worked event traces |
| [language-detection.md](tech-spec/language-detection.md) | `lang: "auto"`: detecting a caller's language on the call, the per-side resolver, the keypad menu fallback, the same-language bridge |
| [audio.md](tech-spec/audio.md) | Internal format, `FrameClock`, gaps, Silero windowing, resampling, mixer math, chunking, pitch heuristic |
| [leg-transports.md](tech-spec/leg-transports.md) | `LegTransport` contract and the Exotel, Plivo, WebRTC and Fake implementations; sample-rate confirmation |
| [api.md](tech-spec/api.md) | `/v1/calls`, `/ws/leg`, provider webhooks, outbound platform events, dial-in, console endpoints |
| [data-model.md](tech-spec/data-model.md) | Postgres tables, background writer, text retention, Redis registry, opt-in recordings |
| [console.md](tech-spec/console.md) | Live console: what it shows, layout, SSE event stream, trust-review use |
| [configuration.md](tech-spec/configuration.md) | `config/pairs.yaml` schema and values, TTS per target language, env vars, global constants |
| [security-privacy.md](tech-spec/security-privacy.md) | DPDP disclosure, voice and cloning, residency, retention, auth, audit log, mistranslation safeguards |

### Plan

| Doc | What it covers |
|---|---|
| [phases.md](plan/phases.md) | Phase 0 (buildathon), Phase 1 (Nov 2026 to Jan 2027), Phase 2 (Feb to Jun 2027); v1 vs v2 features; non-goals |
| [buildathon-10-day-plan.md](plan/buildathon-10-day-plan.md) | Day-by-day milestones and owners, parallel tracks, CP1/CP2, cut list, definition of done |
| [roles-and-ownership.md](plan/roles-and-ownership.md) | What each of the five owns, their modules, "done means", handoff contracts |
| [risks.md](plan/risks.md) | Risk register with likelihood, impact, mitigation, owner and trigger |
| [open-questions.md](plan/open-questions.md) | The six technical questions (by Day 2), product and process questions, spec gaps, answer log |
| [demo-script.md](plan/demo-script.md) | Pre-work checklist, the 3-minute stage script, fallbacks, corrected form answer |

### Engineering

| Doc | What it covers |
|---|---|
| [repo-structure.md](engineering/repo-structure.md) | Target module layout, responsibilities, owners, where each interface and fake lives, dependency rules |
| [dev-setup.md](engineering/dev-setup.md) | From a clean laptop to FakeLeg and WebRTC loopback runs; env vars; real-phone smoke test |
| [exotel-setup.md](engineering/exotel-setup.md) | Exotel values, Voicebot applet with the dynamic URL, `tools/exotel_check.py`, region notes |
| [testing-strategy.md](engineering/testing-strategy.md) | Day 2 fakes rule, unit tests, bench, rating, loopback, PSTN scenarios, chaos tests, CI |
| [latency-engineering.md](engineering/latency-engineering.md) | Stage budget, latency techniques (v1/v2), PSTN-only measurement, timestamps to log |
| [evaluation-and-metrics.md](engineering/evaluation-and-metrics.md) | v1 metrics and targets, corpus, rating process, what we publish, honest limits |
| [deployment.md](engineering/deployment.md) | One-VM Compose stack, Caddy, same-process rule, allowlisting, observability, scaling later |
| [conventions.md](engineering/conventions.md) | Architecture rules, naming, logging, metrics, code style, git and commit style |

## Related

- [Open questions](plan/open-questions.md)
- [Glossary](product/glossary.md)
- [ADR index](architecture/adr/README.md)
