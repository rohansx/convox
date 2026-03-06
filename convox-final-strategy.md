# Convox: Open-Source Voice AI Orchestration Platform
## Strategic Research & Product Plan — Final Version
### Synthesized from multi-source research (Claude, Gemini, Grok) — February 2026

---

## 1. What Is Convox

**Convox is an open-source voice AI orchestration platform. Self-hosted. Any provider. Any language. Any compliance framework.**

Built on Pipecat, Convox adds the dashboard, analytics, compliance tooling, and enterprise features that Pipecat Cloud doesn't offer — while remaining self-hostable and provider-neutral. Think of it as the **Supabase of Voice AI**: a universal platform that anyone can deploy, with opinionated defaults that make specific markets (starting with India) incredibly easy.

### Architecture

```
CONVOX = Open-source voice AI orchestration platform (universal)
         │
         ├── Provider Plugins (swap any STT/TTS/LLM)
         │   ├── NVIDIA PersonaPlex / Riva / Nemotron (full-duplex)
         │   ├── OpenAI Whisper + GPT + TTS
         │   ├── Deepgram + Anthropic + ElevenLabs
         │   ├── Sarvam STT + Gnani Vachana TTS (India sovereign)
         │   ├── Azure Speech Services
         │   ├── Google Cloud Speech
         │   └── [Any new model — plug it in]
         │
         ├── Compliance Modules (optional, per-region)
         │   ├── DPDP (India) — Voice Consent Gateway, audit logs, deletion engine
         │   ├── HIPAA (US Healthcare)
         │   ├── GDPR (Europe)
         │   └── [Future: Brazil LGPD, SOC2, ISO27001]
         │
         ├── Telephony Adapters
         │   ├── Twilio (global)
         │   ├── Exotel / Plivo (India)
         │   ├── Vonage (global)
         │   └── [Any SIP provider]
         │
         ├── Dashboard & Analytics
         │   ├── Visual agent builder (no-code)
         │   ├── Per-call cost analytics (STT + LLM + TTS + telephony breakdown)
         │   ├── Provider A/B testing and smart routing
         │   ├── Transcript viewer + search
         │   └── RBAC, SSO, multi-tenant (enterprise)
         │
         └── Deployment Options
             ├── Self-hosted (Docker, one command)
             ├── Convox Cloud (managed, future revenue model)
             └── On-prem (enterprise air-gapped)
```

### Why This Positioning Wins

| Dimension | "India-Only" (Old) | "Universal Platform, India Beachhead" (New) |
|-----------|--------------------|---------------------------------------------|
| TAM | India: $455M → $1.85B | Global: $10B+ voice AI market |
| OSS community | Limited (India devs only) | Global contributors, India production adopters |
| Investor story | "India voice AI tool" | "Open-source voice AI platform with India beachhead" |
| Provider risk | Tied to Sarvam/Gnani | Provider-neutral; survives any single model pivot |
| Expansion path | Stuck in India | DPDP → HIPAA → GDPR; each module = new market |
| NVIDIA relationship | Limited | PersonaPlex/Riva/NIM integration = potential partner |

---

## 2. Market Opportunity (Cross-Verified)

### Global Voice AI Market

| Metric | Figure | Source |
|--------|--------|--------|
| Global Conversational AI (2025) | $14.29B–$14.79B | Fortune Business Insights, IMARC |
| Global Conversational AI (2034) | $82.46B (21% CAGR) | Fortune Business Insights |
| Global Voice AI Agents (2034) | $47.5B (34.8% CAGR) | Market.us |
| Global Call Center Outsourcing growth (2025-2029) | +$26.3B (4.3% CAGR) | Technavio |
| Voice AI VC investment | $315M (2022) → $2.1B (2024) | Wall Street Journal |
| Enterprise AI voice adoption | 47% of companies deploying | Global Growth Insights |

### India-Specific (Beachhead Market)

| Metric | Figure | Confidence |
|--------|--------|------------|
| India Conversational AI (2024) | $455M | ✅ Verified |
| India Conversational AI (2030) | $1.85B (26.3% CAGR) | ✅ Conservative estimate |
| India Call Center AI (2024→2030) | $103.8M → $452.5M (27.8% CAGR) | ✅ Exact match across sources |
| India Call Center market | $33B | ✅ Verified |
| India BPO sector | $280B, 3M+ employees | ✅ Verified |
| India broader AI market (2030) | Up to $126B | ✅ Inc42 projection |
| India Enterprise AI (2025→2030) | $11B → $71B (6.5x surge) | ✅ Inc42 |
| Voice AI usage growth (India) | 48% YoY | ✅ Verified |
| IndiaAI Mission funding | ₹100B ($12B+) | ✅ Verified |
| GPU subsidies distributed | ₹1B+ to 12+ firms | ✅ Verified |

### Why India Is the Right Beachhead

- **1B+ voice calls daily** across enterprises
- **22 official languages, 200+ dialects** — global platforms can't solve this
- **75-90% of users** prefer regional languages over English
- **DPDP Act** (full enforcement May 2027, ₹250 Cr penalties) creates urgent demand for self-hosted, compliant solutions
- **RBI mandate** requires payment data stored exclusively in India — banks MUST self-host voice systems
- **IndiaAI Mission** provides subsidized GPU compute ($1/hour H100s) for sovereign model deployment
- **Bolna has validated demand**: $700K ARR, 200K calls/day, 1,050+ paying customers — the market is real

---

## 3. NVIDIA Integration: The Full-Duplex Advantage

### NVIDIA PersonaPlex (Released January 2026)

PersonaPlex is a paradigm shift in voice AI. Instead of the traditional cascade (STT → LLM → TTS), it's a **single 7B-parameter model** that listens and speaks simultaneously.

| Feature | PersonaPlex | Traditional Cascade |
|---------|-------------|-------------------|
| Architecture | Single model, full-duplex | 3 separate models, sequential |
| Turn-taking latency | **0.07 seconds** | 0.5–2+ seconds |
| Interruption handling | Native (100% success rate) | Bolted on, unreliable |
| Backchanneling ("mm-hmm") | Natural, contextual | Not possible |
| Customization | Voice prompt (audio) + text prompt (role) | Separate per model |
| License | MIT (code) + NVIDIA Open Model (weights) | Varies |
| Language support | **English only (current limitation)** | Multi-provider = multilingual |
| Hardware | Requires NVIDIA GPU | CPU possible for some models |

**Benchmark**: PersonaPlex scored 3.90 vs Gemini Live's 3.72 on dialog naturalness.

### NVIDIA Riva (Production-Ready)

NVIDIA Riva is a set of GPU-accelerated multilingual speech microservices — already integrated with Pipecat:

- **Riva ASR**: Parakeet models for streaming transcription; Canary models for advanced language support
- **Riva TTS**: Magpie multilingual voices, FastPitch-HifiGAN
- **Languages**: Arabic, English, French, German, **Hindi**, Italian, Japanese, Korean, Mandarin, Portuguese, Russian, Spanish
- **Deployment**: Cloud, on-premise, edge, embedded
- **Pipecat integration**: Official `nvidia-pipecat` SDK exists; NVIDIA has a joint Pipecat voice agent blueprint

### Pipecat + NVIDIA Ecosystem Already Connected

| Integration | Status | Notes |
|-------------|--------|-------|
| `nvidia-pipecat` SDK | ✅ Published on PyPI | Adds Riva ASR, Riva TTS, LLM NIMs, NeMo Agent Toolkit |
| NVIDIA voice-agent-examples repo | ✅ On GitHub | WebSocket + WebRTC examples using Pipecat + Riva |
| NVIDIA NIM blueprint | ✅ On build.nvidia.com | "Voice Agent Framework for Conversational AI" with Pipecat |
| Nemotron Speech ASR | ✅ Streaming integration | 160ms context for low-latency; Daily.co blog post with code |
| PersonaPlex in Pipecat | 🔜 Not yet | But architecture is compatible; would be a Convox contribution |

### Convox NVIDIA Roadmap

| Phase | Integration | Why |
|-------|------------|-----|
| **MVP** | Riva ASR + Riva TTS as provider options (alongside Sarvam/Gnani/Deepgram/etc.) | Already works with Pipecat; Hindi support via Riva |
| **Phase 2** | PersonaPlex as full-duplex English provider option | Dramatically better English conversation quality; 0.07s latency |
| **Phase 3** | PersonaPlex fine-tuned on Indic languages (when available) + GPU subsidy infrastructure | Full-duplex Hindi/Hinglish = killer feature nobody else has |
| **Ongoing** | NVIDIA NIM microservices for guardrails, RAG, agent toolkit | Enterprise-grade additions |

### GPU Subsidy Strategy (India-Specific)

- IndiaAI Mission offers H100 GPUs at **$1/hour** (vs $3-4/hour market rate)
- Convox self-hosted instances running PersonaPlex or Riva on subsidized GPUs = lowest-cost full-duplex voice AI in the world
- This creates a structural cost advantage for Indian enterprise deployments
- **Don't block MVP on this** — but document it as Phase 3 advantage

---

## 4. Competitive Landscape

### Market Map (Universal View)

| Category | Players | Convox Advantage |
|----------|---------|-----------------|
| **Managed SaaS** | Bolna, Retell, Bland | Open source, self-hosted, no vendor lock-in |
| **API Orchestration** | Vapi | Provider-neutral, no cost opacity, self-hosted |
| **Open Source Framework** | Pipecat, LiveKit | Dashboard, analytics, compliance modules, no-code builder |
| **Open Source Platform** | Dograh | Better Pipecat ecosystem, multilingual, compliance-native |
| **Cloud Platform** | Pipecat Cloud | Self-hosted option, visual dashboard, compliance modules |
| **Enterprise Conv AI** | Yellow.ai, Haptik, Uniphore | Open source, faster deployment, transparent pricing |
| **Sovereign Infrastructure** | Sarvam, Gnani, BharatGen | Platform layer on top; they're engines, not orchestrators |

### Direct Competitor Profiles

#### Bolna AI ✅ Fully Verified
- **Funding**: $6.3M seed (Jan 2026), General Catalyst + YC + Blume
- **Traction**: 200K calls/day, 1,050+ paying customers, ~$700K ARR
- **Revenue**: $20K/mo (Sep 2025) → $56K/mo (Dec 2025)
- **Team**: 9 forward-deployed engineers, growing 2-3/month
- **Languages**: 10+ Indian languages, 50+ accents, Hinglish/code-mixing
- **Pricing**: $0.05/min pay-as-you-go; enterprise ₹5/call
- **Open source**: **NO — proprietary** (was previously claimed as OSS)
- **On-prem**: Yes, available for enterprises
- **Convox advantage**: Open source, provider-neutral, universal platform (not India-locked), DPDP compliance tools

#### Dograh AI ⚠️ Traction Unverified
- **Entity**: Zansat Technologies Private Limited
- **License**: BSD 2-Clause, self-hostable
- **Positioning**: "Open-source Vapi alternative" with drag-and-drop builder
- **Language**: English primary
- **Key finding from Grok**: "No verifiable traction data found" — content marketing may outpace actual adoption
- **Convox advantage**: Pipecat ecosystem (vs custom runtime), multilingual native, compliance modules, NVIDIA integration

#### Pipecat Cloud (Daily.co) ✅ Verified
- **Status**: GA January 2026; 1,000+ teams in beta
- **Offering**: Managed container hosting for Pipecat agents
- **Features**: Auto-scaling, telephony, monitoring, HIPAA/GDPR
- **Missing**: No visual dashboard, no no-code builder, no cost analytics, no self-hosted option, no DPDP compliance
- **Convox advantage**: Self-hosted, visual dashboard, compliance modules, no-code builder — everything Pipecat Cloud doesn't have

#### Vapi ✅ Known Player
- **Positioning**: Developer-first API orchestration, BYOK model
- **Weakness**: Cost opacity ($0.33/min effective), US-based data residency
- **Convox advantage**: Open source, self-hosted, transparent pricing, compliance modules

### Sovereign Infrastructure Partners (Not Competitors)

| Partner | What They Provide | Funding | Key Metric |
|---------|-------------------|---------|-----------|
| **Sarvam AI** | LLM (30B, 105B), STT, voice models | $50M+ | IndiaAI Mission sovereign LLM partner |
| **Gnani.ai** | Vachana TTS (12 languages), STT (1M hours trained) | $7.72M + ₹177 Cr govt | ₹56 Cr FY25 → ₹160 Cr FY26 target |
| **BharatGen** | Param2 17B MoE, Indic languages | ₹9B (IndiaAI Mission) | Open source on HuggingFace |
| **Smallest.ai** | Ultra-fast TTS/STT models (53ms TTFT) | $8.26M seed | **Pune-based** — natural partnership |
| **NVIDIA** | PersonaPlex, Riva ASR/TTS, NIM microservices | N/A | Full Pipecat integration exists |

---

## 5. Regulatory Landscape (Compliance Modules)

### Module 1: DPDP (India) — Launch Priority

| Requirement | Implementation in Convox |
|-------------|------------------------|
| **Voice Consent Gateway** | In-call consent capture in user's language, timestamped, purpose-linked, withdrawable |
| **Data residency** | Self-hosted architecture guarantees India-resident processing |
| **72-hour breach notification** | Automated breach detection + notification workflow |
| **Data minimization** | Configurable retention policies, auto-deletion engine |
| **Right to erasure** | One-click deletion of all user voice data + transcripts |
| **Audit logs** | Mandatory 1-year log retention, exportable for compliance audits |
| **SDF obligations** | DPIA templates, DPO dashboard, annual audit support |

**Timeline**: Full DPDP enforcement by **May 13, 2027**. No grace period. ₹250 Cr penalty per violation. 68% of companies still not ready. **This is a NOW procurement cycle.**

**Unique differentiator**: No competitor offers a Voice Consent Gateway as a core product feature. This is Convox's moat in India.

### Module 2: HIPAA (US Healthcare) — Phase 2

- BAA-ready architecture
- PHI handling in voice recordings
- Encryption at rest and in transit
- Access controls and audit trails
- Pipecat Cloud is already pursuing HIPAA — Convox offers the self-hosted alternative

### Module 3: GDPR (Europe) — Phase 3

- Consent management (similar to DPDP but different requirements)
- Right to be forgotten
- Data portability
- Cross-border transfer mechanisms

### The Compliance Moat

Each compliance module Convox builds **expands the addressable market** and creates switching costs:

```
DPDP module → unlocks Indian BFSI, healthcare, government ($1.85B by 2030)
HIPAA module → unlocks US healthcare voice AI ($150B potential savings)
GDPR module → unlocks European enterprise ($3.8B EU conversational AI)
```

No open-source voice AI platform offers modular, region-specific compliance today. This is greenfield.

---

## 6. Unit Economics

### Pricing Benchmarks (Global)

| Model | Rate | Used By |
|-------|------|---------|
| Pay-as-you-go | $0.07–$0.18/min | Vapi, Retell (startups) |
| Bundled | ₹6–₹14/min (~$0.07–$0.17) | Bolna (Indian SMEs) |
| SaaS subscription | $350–$1,250/month | CloudTalk, mid-market |
| Enterprise on-prem | Custom volume | Banks, insurance, healthcare |
| **Self-hosted open source** | **~$0.01/min** (infra only) | Tech teams running own stack |

### The Self-Hosted Cost Advantage

| Item | Managed Platform | Self-Hosted Convox |
|------|-----------------|-------------------|
| Platform fee | $0.05–$0.15/min | $0 (open source) |
| STT | Included (marked up) | Direct to provider (Sarvam, Deepgram, Riva) |
| LLM | Included (marked up) | Direct to provider or self-hosted |
| TTS | Included (marked up) | Direct to provider (Gnani, ElevenLabs, Riva) |
| Telephony | Included (marked up) | Direct to Twilio/Exotel |
| **Total at scale** | **$0.07–$0.20/min** | **$0.01–$0.03/min** |
| **Savings** | — | **70-90% lower** |

At high volume (100K+ minutes/month), the self-hosted cost advantage is enormous. This is Convox's core economic proposition.

### India-Specific: GPU Subsidy Math

- IndiaAI Mission: H100 at $1/hour
- PersonaPlex/Riva on subsidized GPU: even lower inference costs
- Human agent in India: ₹50,000–₹80,000/month
- AI agent (self-hosted Convox): ₹6,000–₹14,000/month equivalent
- **Cost reduction: 70-88%**
- Break-even: ~1.3 years for enterprise deployments

---

## 7. Product Roadmap

### Phase 1: MVP (8-12 weeks) — "Hello World to Hindi Voice Call"

**Goal**: An Indian bank can make 100 Hindi voice calls from a self-hosted instance with DPDP-compliant consent logging.

| Feature | Details |
|---------|---------|
| **Runtime** | Pipecat-based agent runtime |
| **Provider plugins** | Sarvam STT, Gnani Vachana TTS, NVIDIA Riva (Hindi), OpenAI Whisper, Deepgram — all swappable |
| **Languages** | Hindi, English, Hinglish out of the box |
| **Dashboard** | Basic web UI: create agent, configure provider, test via web call, view transcripts |
| **Telephony** | Exotel (India) + Twilio (global) |
| **Compliance** | Voice Consent Gateway v1 (consent capture + timestamped log) |
| **Deployment** | One-command Docker setup (`docker compose up`) |
| **License** | Apache 2.0 |

### Phase 2: Platform (3-6 months) — "Production-Ready"

| Feature | Details |
|---------|---------|
| **No-code builder** | Visual workflow builder for agent design |
| **Languages** | +5 Indian languages (Tamil, Telugu, Bengali, Marathi, Kannada) via Sarvam/Gnani |
| **PersonaPlex** | Full-duplex English conversation as provider option |
| **DPDP module** | Full compliance: consent tracking, retention policies, audit logs, breach workflow, deletion engine |
| **Cost analytics** | Per-call cost breakdown (STT + LLM + TTS + telephony) |
| **Provider routing** | A/B testing, smart routing, fallback chains |
| **Batch calling** | Outbound campaign management |
| **CRM integrations** | Salesforce, Zoho, Freshworks, HubSpot |
| **RBAC + SSO** | Enterprise access control |

### Phase 3: Scale (6-12 months) — "Enterprise + Global"

| Feature | Details |
|---------|---------|
| **HIPAA module** | US healthcare compliance |
| **GDPR module** | European compliance |
| **Managed cloud** | Convox Cloud — usage-based pricing (revenue model) |
| **Multi-tenant** | Full multi-tenant architecture |
| **Behavioral analytics** | Prosody analysis (pitch, hesitation, stress) for collections, recruitment, fraud detection |
| **PersonaPlex Indic** | Fine-tuned full-duplex for Hindi/Hinglish (when models available) + GPU subsidy infrastructure |
| **On-prem guides** | AWS Mumbai, Azure India, GCP Mumbai, bare metal |
| **SOC2 / ISO27001** | Certification documentation |
| **50+ accents** | Accent optimization across Indian + global accents |

---

## 8. Go-to-Market Strategy

### Positioning

> **Convox**: The open-source voice AI orchestration platform. Self-hosted. Any provider. Any language. Any compliance framework.
>
> *Ships with India-optimized defaults: sovereign models, DPDP compliance, Hindi/Hinglish on day one.*

### GTM Sequence

```
Phase 1: LAUNCH
├── Open-source platform on GitHub
├── Works globally with any provider (OpenAI, Deepgram, ElevenLabs, NVIDIA Riva, etc.)
├── Developer community building (global)
└── Content: "self-hosted alternative to Vapi/Retell" (universal appeal)

Phase 2: BEACHHEAD — India
├── DPDP compliance module + Voice Consent Gateway
├── Sarvam/Gnani sovereign model integration (pre-configured "India stack")
├── Exotel/Plivo Indian telephony
├── Target: BFSI collections, KYC, complaint handling
├── Forward-deployed engineering for first 5-10 enterprise customers
└── Content: "DPDP-ready voice AI" + Bolna comparisons

Phase 3: EXPAND
├── HIPAA module → US healthcare market
├── GDPR module → European enterprise market
├── PersonaPlex integration → best-in-class English conversations
├── Convox Cloud (managed offering = revenue)
└── Partner program (SI/consulting firms)
```

### Target Customers

**India beachhead (Phase 1-2):**
1. Indian BFSI — collections, KYC, complaints (RBI data residency = must self-host)
2. Indian BPO/contact centers — 1.1M+ deployments seeking AI augmentation
3. Indian e-commerce — delivery coordination in regional languages
4. Indian recruitment — screening at scale

**Global expansion (Phase 3+):**
5. US healthcare — HIPAA-compliant voice AI (self-hosted)
6. European enterprise — GDPR-compliant voice AI
7. Global developer community — anyone wanting self-hosted voice AI without vendor lock-in

### Revenue Model (Supabase Playbook)

| Tier | Price | Target |
|------|-------|--------|
| **Open Source** | Free forever | Developers, startups, community |
| **Convox Cloud** | Usage-based (per minute) | Teams wanting managed hosting |
| **Enterprise** | Annual contract ($25K-$100K+) | Support, compliance certs, on-prem deployment, SLA |
| **Forward-deployed engineering** | Custom | Large enterprise implementation (Bolna model, proven in India) |

---

## 9. Partnership Strategy

| Partner | Relationship | Value to Convox |
|---------|-------------|-----------------|
| **NVIDIA** | Technology partner | PersonaPlex + Riva + NIM integration; joint marketing via Pipecat blueprint |
| **Smallest.ai** (Pune!) | Model integration | Ultra-fast TTS ($0.01/min at scale); co-located for collaboration |
| **Sarvam AI** | Model integration | Sovereign LLM + STT for India stack |
| **Gnani.ai** | Model integration | Vachana TTS/STT (12 Indian languages) |
| **Pipecat / Daily.co** | Framework partner | Convox contributes back to Pipecat; potential featured ecosystem project |
| **Exotel** | Telephony partner | Indian number provisioning, SIP |
| **Zoho / Freshworks** | CRM integration | Indian enterprise ecosystem access |

---

## 10. Competitive Positioning (Final Matrix)

| Dimension | Vapi | Bolna | Dograh | Pipecat Cloud | **Convox** |
|-----------|------|-------|--------|---------------|-----------|
| Open source | ❌ | ❌ | ✅ | ❌ | ✅ |
| Self-hosted | ❌ | ✅ On-prem | ✅ | ❌ | ✅ Core |
| Provider-neutral | ✅ BYOK | ⚠️ Custom | ⚠️ Custom | ✅ Pipecat | ✅ Pipecat + all |
| NVIDIA PersonaPlex | ❌ | ❌ | ❌ | 🔜 | ✅ Roadmap |
| No-code builder | ❌ | ✅ | ✅ | ❌ | ✅ Phase 2 |
| Indian languages | ❌ | ✅ 10+ | ❌ English | ❌ | ✅ Day 1 |
| Global languages | ✅ | ⚠️ India focus | ✅ English | ✅ | ✅ Via providers |
| DPDP compliance | ❌ | ⚠️ Basic | ⚠️ Marketing | ❌ | ✅ Native module |
| HIPAA compliance | ❌ | ❌ | ❌ | 🔜 | ✅ Roadmap |
| GDPR compliance | ❌ | ❌ | ❌ | ✅ | ✅ Roadmap |
| Voice Consent Gateway | ❌ | ❌ | ❌ | ❌ | ✅ **Unique** |
| Cost analytics | ❌ | ⚠️ Generic | ⚠️ Basic | ⚠️ Monitoring | ✅ Phase 2 |
| RBAC/SSO | ❌ | ❌ | ❌ | ⚠️ Basic org | ✅ Phase 2 |
| Pricing transparency | ❌ ($0.33 effective) | ✅ | ✅ Free OSS | 💰 Per container | ✅ $0 platform fee |

---

## 11. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Solo founder execution** | Very High | Find co-founder. Leverage OSS community. Ship MVP in 8-12 weeks. Don't build everything at once. |
| **Bolna head start ($6.3M, YC)** | High | Different buyer persona: enterprises wanting control + open source. Bolna's enterprise motion is still early (75% self-serve). Universal platform vs India-locked. |
| **Pipecat Cloud adds dashboard/compliance** | Medium | They're infra, not application. Adding DPDP/HIPAA/no-code is a major pivot for Daily.co. Also: Convox is self-hosted, which Pipecat Cloud will never be. |
| **Dograh competes on OSS** | Medium-Low | Grok found no verifiable traction. English-first, custom runtime, no NVIDIA integration, no compliance modules. |
| **Sarvam/Gnani build own platforms** | Medium | They're model providers wanting distribution. Gnani's ₹160 Cr revenue target is from enterprise services, not platform. Partnership > competition. |
| **Globals enter India (ElevenLabs)** | Medium | Regulatory + linguistic barriers are high. DPDP Consent Manager registration requires India-incorporated entities. |
| **PersonaPlex stays English-only** | Medium | MVP doesn't depend on it. Cascade architecture (STT→LLM→TTS) works for all languages today. PersonaPlex is a future upgrade. |
| **Enterprises slow to adopt** | Medium | Bolna's 200K calls/day + $700K ARR proves demand. DPDP deadline (May 2027) creates urgency. |

---

## 12. Summary: Why Build This, Why Now

### The Thesis

The voice AI market is exploding ($14B → $82B by 2034). The infrastructure layer (models) is being commoditized — sovereign models are free, NVIDIA open-sources PersonaPlex, providers multiply monthly. But the **orchestration layer** — the platform that connects models to enterprise workflows with compliance, analytics, and ease of use — remains fragmented and mostly proprietary.

### The Gap

No open-source voice AI platform exists that combines:
1. Provider-neutral architecture (any STT/TTS/LLM including NVIDIA PersonaPlex)
2. Self-hosted deployment (Docker, on-prem, any cloud)
3. Region-specific compliance modules (DPDP, HIPAA, GDPR)
4. Visual dashboard with no-code agent builder
5. Cost analytics and provider routing

Pipecat is the closest — but it's a framework, not a platform. Pipecat Cloud is managed-only. Bolna is proprietary and India-locked. Dograh is English-first with unverified traction. Vapi has cost opacity and US data residency.

### The Timing

- DPDP full enforcement: May 2027 (enterprises buying NOW)
- PersonaPlex released: January 2026 (full-duplex goes open source)
- Pipecat Cloud GA: January 2026 (validates the platform opportunity)
- IndiaAI Mission GPU subsidies: Active now ($1/hour H100s)
- Bolna's traction: Proves the market in India specifically
- Voice AI VC surge: $2.1B invested in 2024 alone

### The Plan

1. **Build**: Open-source, universal voice AI orchestration platform on Pipecat
2. **Launch**: India beachhead with DPDP compliance + sovereign model defaults
3. **Grow**: Global expansion via HIPAA (US) → GDPR (EU) compliance modules
4. **Monetize**: Managed cloud + enterprise contracts (Supabase model)

### Critical Next Step

Ship an MVP in 8-12 weeks:
- One-command Docker setup
- Hindi/English/Hinglish voice calls
- Sarvam + Gnani + NVIDIA Riva as provider options
- Basic dashboard
- Voice Consent Gateway
- Open source on GitHub

Everything else follows from there.
