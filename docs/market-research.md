# Market Research: Voice-AI Testing & Observability

*Research date: July 2026. Question: where is the durable, unclaimed opportunity in the voice-AI stack?*

---

## TL;DR

**The opportunity is the open-source, self-hosted voice-agent testing platform.** The orchestration/builder layer (Retell, Vapi, Bland, Bolna) is commoditized. The testing/observability layer is the fastest-emerging adjacent market with strong funding validation (Coval $31M, Cekura $2.4M, Hamming ~$4.5M, Bluejay $4M) — and **not a single maintained open-source player exists in it**. The one OSS attempt (fixa, YC F24) died at 117 GitHub stars, while dograh — a self-hosted Vapi/Retell alternative — hit ~5,000 stars in under a year, proving strong appetite for self-hosted voice-AI infra. Nobody has built the testing/observability layer next to it.

Building another Retell would be a mistake. The right move is the testing layer that sits *above* Retell/Vapi/LiveKit/Pipecat as a neutral QA layer — integrating with all of them instead of competing with any of them.

---

## 1. What Cekura provides (the feature bar to meet)

Cekura (ex-Vocera, YC F24, $2.4M seed led by YC, ~75 customers, Cisco Webex partner) covers the full agent QA lifecycle:

**Pre-production testing**
- **Scenarios + Evaluators**: simulated caller (who they are, what they want, how they behave) + success criteria, run against your live agent over real telephony/WebRTC/WebSocket, auto-evaluated.
- **Auto test-case generation**: fine-tuned model generates 10–1000s of scenarios from just the agent's prompt.
- **50+ personas, 8 controllable dimensions**: language, accent, gender, emotion (50+ states), speed, volume, interruption behavior, background noise (30+ environments). Custom cloned voices (Cartesia).
- **Multilingual**: 30+ languages incl. 9 Indian languages, code-switching mode.
- **Audio-layer testing** (key differentiator vs transcript-only): barge-in/interruption latency, TTS overrun, VAD accuracy, ASR accuracy under noise, garbled-audio detection. Failure attribution by layer (STT vs LLM vs TTS).
- **IVR/DTMF testing**, adversarial/red-team scenarios, network degradation.
- **Load testing**: 10 → 2,000+ concurrent calls.
- **Regression/CI**: suites re-run across agent versions, latency P50/P90 tracking, GitHub Actions / GitLab / Jenkins native.

**Production observability**
- Every live call scored on the same metrics; dashboards, Slack/email alerts, drift detection.
- **Replay**: re-run real production calls as tests against new agent versions.
- OpenTelemetry-native tracing; LiveKit/Pipecat tracing integrations.

**Enterprise + dev surface**
- SOC 2/HIPAA/GDPR, RBAC, **on-prem/VPC/air-gapped self-hosting (enterprise-tier upsell)**.
- REST API, MCP server (84+ tools for Claude Code/Cursor), webhooks.
- Public orchestration benchmark (benchmarks.cekura.ai) comparing Retell/Vapi/Pipecat/LiveKit/Synthflow/ElevenLabs with one identical agent.

**Pricing**: credit-based; Developer $30/mo (~150 test minutes), Growth ~$4K/yr, Enterprise custom. Integrations: Vapi, Retell, Bland, ElevenLabs, LiveKit, Pipecat, Cisco Webex, Twilio/SIP.

**How it works technically**: Cekura's test agents *call your agent* — over real phone lines, WebRTC rooms, or WebSockets — speaking with persona-modulated TTS (noise, interruptions, emotion, language). Calls are recorded, diarized, transcribed; LLM judges evaluate transcript **and audio**; latency instrumented per turn. Outbound agents are tested by having your agent call a Cekura-controlled number (caller-ID-routed).

## 2. Retell AI — and why not to clone it

Retell (YC W24): agent **builder** platform — prompt/flow-builder agents, multi-LLM, multi-TTS with fallback, telephony/SIP, batch calling, knowledge base, post-call analytics. ~$50M ARR with ~30 people on only ~$5M raised; 50M+ calls/month. Usage-priced (~$0.07/min platform + LLM + telephony ≈ $0.13–0.31/min all-in).

Why cloning it is the wrong move:
- **12+ credible head-to-head competitors**: Vapi ($50M Series B, $500M valuation, won Amazon Ring over 40 rivals), Bland ($100M+ raised, owns its speech models), ElevenLabs Agents (bundling from TTS dominance), Synthflow, Bolna (India niche, $6.3M), LiveKit ($1B valuation) and Pipecat underneath, OpenAI Realtime compressing the whole pipeline into one model.
- Feature parity everywhere: flow builder, multi-LLM, KB/RAG, batch calls, analytics — converging prices, bake-off-driven sales.
- a16z's read: value moving "from infrastructure to applications" — pipes margin is compressing.
- Retell's own moves confirm where value is going: it shipped **Retell Assure** (Dec 2025) — automated QA monitoring 100% of production calls — because the *trust layer* is the margin-rich retention moat.

**The useful "mix" of the two ideas**: don't compete with Retell — test Retell. Retell/Vapi/Pipecat all co-market with Cekura/Hamming via partner pages; platforms treat neutral external QA as complementary. Their native testing is text/LLM-level and misses audio-layer failures, adversarial testing, load, and cross-platform regression.

## 3. Competitive landscape (testing/evals/observability)

| Player | Position | Funding | Open source? |
|---|---|---|---|
| **Coval** (YC S24) | Simulation + observability; enterprise logos (Zoom, ServiceNow, GEICO) | **$31M** ($28M Series A, Jun 2026) | No |
| **Cekura** (YC F24) | Full lifecycle; cheapest self-serve ($30/mo); multilingual; Cisco partner | $2.4M seed | No |
| **Hamming** (YC S24) | Audio-layer testing narrative ("42% of failures invisible to transcripts"); 50K concurrent load claims | ~$4.5M | No |
| **Bluejay** (YC X25) | "Digital humans" personas; $4M seed (Floodgate) | $4M | No |
| **Roark** (YC W25) | Replay production calls with cloned caller voices | ~$500K disclosed | No |
| **fixa** (YC F24) | Phone-call-based testing, Python | — | **Yes — dead at 117 stars, dormant since Q1 2026** |
| Evalion, Canonical, Braintrust, Cyara | niche/legacy/general | — | No |

**Platform-native testing (the "good-enough" threat from below)**: Vapi Test Suites, Retell simulation + Assure, ElevenLabs simulate-conversation API (text-only), LiveKit `agents.evals` (text-turn level), Pipecat Evals v1.4 (audio-in-the-loop but Pipecat-only). All framework-locked and mostly transcript-level.

**Open-source state of play** (verified star counts, July 2026):
- Dedicated voice testing OSS: fixa 117★ (dead), voice-lab 173★ (side project), voicetest 28★, decibench 13★, voicegateway 11★. **Nothing maintained above ~200 stars.**
- General eval OSS: promptfoo 23.7K★, deepeval 17.2K★, Langfuse 32K★, Phoenix 10.8K★ — **none do telephony simulation, synthetic callers, audio metrics, or load testing**. Langfuse is closest for passive observability (audio attachments in traces) but has no simulation/scoring.
- **dograh 5,059★ in ~10 months** — self-hosted Vapi/Retell alternative — is the existence proof for self-hosted voice-AI demand.

## 4. Concrete market gaps (the opportunity)

1. **No self-hosted open-source Cekura/Hamming/Coval alternative exists.** Every credible product is closed SaaS; Cekura sells self-hosting as an enterprise upsell — meaning enterprises demonstrably want it. This is the wedge.
2. **No OSS telephony load testing** (concurrent SIP/Twilio call generation with audio metrics). Commercial-only today.
3. **No OSS production call monitoring** with voice-native metrics (per-turn latency percentiles, interruptions, WER drift, SNR, sentiment). Langfuse/Phoenix give traces, not call QA.
4. **Multilingual/Indic testing is a documented quality gap**: vendors claim 30+ languages but real Hindi/Tamil accuracy reportedly drops to 60–70%; Hindi WER in high teens vs <10% for English; Hinglish code-switching largely untested; nobody publishes dialect-level benchmarks (see VoiceAgentBench, arXiv 2510.07978).
5. **No adopted open testing standard/benchmark** — every vendor scores differently; decibench (13★) is trying and failing alone.
6. **Cross-platform test portability** — only voicetest (28★) attempts import-from-Retell/Vapi/Bland/LiveKit in OSS.

Developer pain confirming demand: manual call-and-listen QA is the universally cited founding complaint of every company in this space; LLM-judge flakiness pushes demand for deterministic assertions; turn-taking (not transcription) is the hard problem (565-point HN thread).

## 5. Recommendation: what to build

**Product**: *Open-source, self-hosted voice-agent testing & observability platform* — "the open-source Cekura." Working frame: test any agent (Retell, Vapi, LiveKit, Pipecat, Bland, ElevenLabs, or raw SIP/WebSocket) from one self-hosted stack.

**Why this beats the alternatives considered**:
- vs orchestration (a Bolna/Retell clone): commoditized, capital-intensive, price-compressed.
- vs Retell clone: 12+ funded competitors, value migrating out of that layer.
- vs closed-SaaS Cekura clone: you'd be 18 months behind four funded YC startups with no distribution. Open source *is* the distribution strategy — and the only unclaimed position.

**Build order (wedge → platform):**
1. **Simulation engine (the core)** — a test agent that calls your agent via WebSocket/WebRTC first (cheap, no telephony bills), then Twilio/SIP. Persona-driven simulated callers (language, accent, emotion, interruptions, background noise via audio mixing). Scenario spec in YAML/code (pytest-like DX). A pluggable provider layer (Sarvam/OpenAI STT-TTS) supplies the simulated caller's voice stack.
2. **Evaluation** — hybrid scoring: deterministic assertions (tool called, call ended, DTMF path, latency budget, regex on transcript) + LLM-judge metrics (instruction-following, relevancy, sentiment), with audio-layer metrics (per-turn latency P50/P90, interruption latency, silence timeouts, WER via re-transcription). Deterministic-first is a stated differentiator — LLM-judge flakiness is a known complaint.
3. **CI/CD** — GitHub Action + CLI: run suite on PR, fail build on regression, post report. This is what makes it feel like "pytest for voice agents" and drives OSS adoption.
4. **Platform connectors** — one-command import of agent configs from Retell/Vapi/ElevenLabs APIs; dynamic variables passed through triggers; auto-sync of prompts.
5. **Production observability** — OTel ingest + webhook ingestion of production calls from Retell/Vapi; every call scored on the same metrics; dashboard (reuse convox React app), Slack alerts, replay-call-as-test.
6. **Load testing** — concurrent call generation with the same personas; surface concurrency limits and latency degradation. No OSS tool does this at all.
7. **Differentiators to own**: (a) **Indic/multilingual testing** — Hinglish code-switching personas, dialect-level WER benchmarks, Sarvam/Indic model support — a gap every vendor claims to cover and none does; (b) **open benchmark** — publish a public leaderboard testing Retell vs Vapi vs Pipecat etc. with the OSS tool (Cekura's benchmarks.cekura.ai but reproducible) — this is a marketing engine.

**Monetization path (later)**: standard open-core — OSS self-hosted core free; paid cloud (managed telephony numbers, hosted dashboards, team features); enterprise (SSO/RBAC, compliance reports, SLAs). Cekura charging $30/mo self-serve proves land-and-expand works at the low end; Coval's $4.5K/mo enterprise tier shows the ceiling.

**Risks & mitigations**:
- *Platforms bundle QA (Retell Assure, Vapi test suites)* → they can never be the neutral cross-platform layer, and none will ever be self-hostable or test competitors' agents. Neutrality + self-hosting is the moat.
- *fixa died — does that disprove OSS here?* fixa was two founders who pivoted away (YC company, abandoned repo); scope was thin (transcript-level judging over phone calls). Market timing is also better now: 2026 has far more production voice agents needing QA than Jan 2025.
- *LLM-judge cost of running evals* → deterministic-first design + local/cheap judge model support (self-hosted = bring your own keys, a feature closed SaaS can't match).
- *Telephony cost of test calls* → WebSocket/WebRTC-first testing (free) with telephony as the "full-fidelity" tier, same as Cekura's channel coverage.

**Where the product design landed**: see [product-overview.md](product-overview.md), [features.md](features.md), and [architecture.md](architecture.md).

---

## Sources

Cekura: cekura.ai (+ /pricing, /changelog, /blogs, /partners/*), docs.cekura.ai, benchmarks.cekura.ai, ycombinator.com/companies/cekura-ai, mlq.ai seed-funding coverage, coval.ai/blog/coval-vs-cekura, docs.pipecat.ai/pipecat/evals/platforms/cekura.

Retell & platforms: retellai.com (docs/blog via search index), GlobeNewswire (Assure launch Dec 2025; omnichannel Jan 2026), arr.club ($50M ARR), TechCrunch (Vapi Series B; Bolna seed), PRNewswire (Bland Series C; Coval Series A), livekit.com/blog/livekit-series-c, daily.co/pricing/pipecat-cloud, a16z.com/ai-voice-agents-2025-update.

Testing/OSS landscape: github.com/fixadev/fixa (117★), saharmor/voice-lab (173★), langwatch/scenario (933★), voicetestdev/voicetest (28★), unforkopensource-org/decibench (13★), dograh-hq/dograh (5,059★), pipecat-ai/pipecat (13.8K★), livekit/agents (11.5K★), promptfoo (23.7K★), deepeval (17.2K★), langfuse (32K★), Arize-ai/phoenix (10.8K★); hamming.ai resources, speechmatics.com 11-platform roundup, news.ycombinator.com/item?id=47224295, arxiv.org/pdf/2510.07978 (VoiceAgentBench).
