# Convox — Phase Breakdown & Milestones

> Last updated: February 2026
> Status: Pre-implementation planning

---

## Phase Philosophy

Each phase must produce something **real and deployable** — not a prototype or internal tool. The exit criteria for each phase is a specific user story that works end-to-end. No phase ends with "X% complete."

---

## Phase 1: MVP — "Hello World to Hindi Voice Call"

**Duration**: 8–12 weeks from start
**Exit criteria**: An Indian bank's dev team can `git clone` + `docker compose up`, create a Hindi agent, and make 100 production-grade DPDP-compliant calls.

### What Gets Built

| Week | Deliverable |
|------|-------------|
| 1–2 | Repo skeleton, Docker Compose, DB schema, Core API scaffolding, auth |
| 3–4 | Provider plugin layer: Sarvam STT, Gnani TTS, OpenAI (LLM + fallback STT/TTS) |
| 5 | Pipecat pipeline assembly from agent config; WebSocket audio bridge |
| 6 | Exotel + Twilio telephony adapters; inbound + outbound call handling |
| 7 | Basic dashboard: create agent, configure providers, run test call in browser |
| 8 | DPDP Voice Consent Gateway v1 (consent prompt + timestamped storage) |
| 9 | Transcript viewer, session history, basic cost display |
| 10 | NVIDIA Riva ASR + TTS as provider options (Hindi support) |
| 11 | End-to-end testing, security audit, documentation |
| 12 | Public GitHub release + demo video + README |

### Provider Support at MVP Exit

| Category | Providers | Notes |
|----------|-----------|-------|
| STT | Sarvam, OpenAI Whisper, NVIDIA Riva, Deepgram | All swappable in agent config |
| LLM | OpenAI GPT-4o, Anthropic Claude | Enough for any use case |
| TTS | Gnani Vachana, NVIDIA Riva, OpenAI TTS, ElevenLabs | Hindi + English covered |
| Telephony | Exotel, Twilio | India + global covered |

### Dashboard at MVP Exit

- Login / API key management
- Create/edit/delete agents (JSON config form — no visual builder yet)
- Test call: in-browser call using WebRTC
- Session list: last 100 calls with status, duration, cost
- Transcript viewer: per-session conversation view
- DPDP: consent log viewer (read-only)

### MVP: What Is NOT Included

- Visual no-code agent builder (Phase 2)
- Cost analytics charts (Phase 2)
- Provider A/B testing (Phase 2)
- Batch/campaign outbound calling (Phase 2)
- CRM integrations (Phase 2)
- RBAC / SSO (Phase 2)
- HIPAA or GDPR modules (Phase 3)
- Managed Convox Cloud (Phase 3)

### MVP Success Metrics

- [ ] `docker compose up` to first call: under 15 minutes
- [ ] Hindi call works with Sarvam + Gnani + OpenAI
- [ ] DPDP consent logged for every call (with module enabled)
- [ ] 10 beta users (target: India-based devs/BFSIs) running on their own infra
- [ ] GitHub repo: at least 50 stars in first 2 weeks

---

## Phase 2: Platform — "Production-Ready"

**Duration**: 3–6 months post-MVP
**Exit criteria**: A mid-size Indian BPO can run a 10-agent outbound campaign, view cost analytics by provider, and pass a DPDP compliance review.

### Features

#### 2A: Full DPDP Compliance Module (Month 1–2)

| Feature | Description |
|---------|-------------|
| Consent withdrawal | Users can withdraw consent; auto-purges future usage |
| Retention policies | Configurable TTL per data type; auto-deletion cron |
| Deletion engine | Full right-to-erasure: transcripts, audio, consent records |
| Audit log | Immutable 1-year log of all access + modification events |
| Breach notification | Automated workflow: detect → notify DPO → 72hr escalation |
| DPIA templates | Pre-filled templates for common use cases (collections, KYC) |
| DPO dashboard | Single-pane view of all compliance events |

#### 2B: Visual Agent Builder (Month 1–3)

- Drag-and-drop pipeline configuration (@xyflow/react)
- Node types: STT, LLM, TTS, Conditional, Function Call, Compliance Hook
- Live preview: test the pipeline before deploying
- Export/import as JSON (agent config schema)
- Template library: pre-built agents for collections, KYC, recruitment

#### 2C: Cost Analytics (Month 2)

- Per-call cost breakdown: STT + LLM + TTS + telephony (exact amounts)
- Cost over time: daily/weekly/monthly charts
- Provider comparison: side-by-side cost/latency/quality view
- Budget alerts: notify when monthly spend exceeds threshold
- Cost export: CSV/JSON for finance teams

#### 2D: Provider Routing (Month 2–3)

- A/B testing: split traffic between providers, compare cost/quality
- Fallback chains: if Sarvam fails, fall back to Deepgram
- Smart routing: route by language (Hindi → Sarvam, English → Deepgram)
- Provider health monitoring: real-time error rates + latency

#### 2E: PersonaPlex Integration (Month 3)

- NVIDIA PersonaPlex as a full-duplex provider option (English)
- Provider interface wrapper: PersonaPlex appears as a single "provider"
- Cost tracking: unified cost model for full-duplex (no separate STT/LLM/TTS)
- A/B testing PersonaPlex vs cascade for English calls

#### 2F: Batch Outbound Campaigns (Month 3–4)

- Upload CSV of phone numbers + agent config
- Scheduling: time windows, rate limits, DNC list support
- Campaign dashboard: completion rate, answer rate, cost/call
- Real-time campaign monitoring (stop/pause/resume)
- Post-campaign analytics export

#### 2G: Indian Language Expansion (Month 4)

- +5 languages via Sarvam/Gnani: Tamil, Telugu, Bengali, Marathi, Kannada
- Language detection: auto-route to correct provider
- Language-specific voice consent prompts for DPDP

#### 2H: CRM Integrations (Month 4–5)

- Zoho CRM: call notes + transcript sync
- Freshworks CRM: same
- HubSpot: same
- Generic webhook: structured call summary to any system
- Salesforce: Phase 2 stretch goal

#### 2I: Enterprise Access Control (Month 5–6)

- RBAC: Owner, Admin, Developer, Analyst, Read-only roles
- SSO: SAML 2.0 (Okta, Azure AD) + Google OAuth
- Multi-tenant: team isolation, per-team provider configs
- Audit log: all user actions logged with actor + timestamp
- IP allowlist: optional restriction to corporate IPs

### Phase 2 Success Metrics

- [ ] A 10,000-call campaign runs successfully on self-hosted instance
- [ ] Cost analytics accurate to ±2% vs actual provider invoices
- [ ] Visual builder used to create agent without touching JSON
- [ ] 500+ GitHub stars
- [ ] 3+ Indian enterprise pilot customers in production
- [ ] PersonaPlex A/B test shows measurable latency improvement vs cascade

---

## Phase 3: Scale — "Enterprise + Global"

**Duration**: 6–12 months post-Phase 2
**Exit criteria**: A US healthcare company self-hosts Convox for HIPAA-compliant patient scheduling. A European enterprise runs GDPR-compliant voice agents. Convox Cloud is live with paying managed customers.

### Features

#### 3A: HIPAA Compliance Module (Month 1–3)

- PHI identification + redaction in transcripts (configurable)
- Encryption: AES-256 at rest; TLS 1.3 in transit
- BAA-ready architecture: documentation for signing BAAs with Convox
- Minimum necessary access controls
- Workforce training documentation (HIPAA requirement)
- US-only data residency option (ensure no data leaves US region)
- Audit log: HIPAA-grade logging of all PHI access

#### 3B: GDPR Module (Month 2–4)

- Consent management: GDPR-flavored (different from DPDP)
- Data portability: export all user data in machine-readable format
- Right to be forgotten: same deletion engine, GDPR-configured
- Cross-border transfer: SCCs + documentation for non-EU transfers
- DPA templates: pre-filled Data Processing Agreements
- Cookie notice: if using Convox Cloud web UI

#### 3C: Convox Cloud — Managed Offering (Month 3–6)

- Hosted version of Convox (no self-hosting needed)
- Usage-based pricing: per-minute of call time
- Multi-tenant: full isolation between customers
- Automatic scaling: handle 10–10,000+ concurrent calls
- SLA: 99.9% uptime for Enterprise tier
- Managed compliance: DPDP / HIPAA / GDPR pre-configured
- Onboarding: guided setup in < 30 minutes

Revenue model:
| Tier | Price | Features |
|------|-------|---------|
| Starter | $99/mo | 1,000 min/mo included; $0.05/min overage |
| Growth | $499/mo | 10,000 min/mo; cost analytics; all providers |
| Enterprise | $2,500+/mo | Unlimited; dedicated support; compliance certs; SLA |

#### 3D: PersonaPlex Indic (Month 4–8, timeline depends on NVIDIA)

- Fine-tuned PersonaPlex on Hindi/Hinglish (when models available from NVIDIA or community)
- Full-duplex Hindi: industry-first capability
- IndiaAI Mission GPU subsidy integration: documentation for deploying on subsidized H100s
- Benchmark report: PersonaPlex Hindi vs cascade Hindi latency/quality

#### 3E: Behavioral Analytics (Month 5–8)

- Prosody analysis: pitch, hesitation, stress detection per turn
- Use cases: fraud detection (unusual speech patterns), recruitment screening, collections effectiveness
- Dashboard: visual overlays on transcript timeline
- API: prosody data available via webhook + transcript export

#### 3F: On-Prem Deployment Guides (Month 6)

- AWS Mumbai: full CloudFormation template
- Azure India: ARM template
- GCP Mumbai: Terraform module
- Bare metal: detailed guide for on-prem Linux servers
- Kubernetes: Helm chart for K8s deployments

#### 3G: SOC2 Type II / ISO27001 (Month 8–12)

- Scope: Convox Cloud managed service
- Evidence collection during earlier phases
- Third-party audit engagement
- Required for US healthcare and large enterprise deals

### Phase 3 Success Metrics

- [ ] 1 US healthcare customer in production with HIPAA module
- [ ] 1 European enterprise customer with GDPR module
- [ ] Convox Cloud at $10K MRR within 3 months of launch
- [ ] 2,000+ GitHub stars
- [ ] PersonaPlex Indic benchmark published
- [ ] SOC2 audit initiated

---

## Milestone Summary

```
Week 1-2    ████ Repo scaffolding + Docker + DB schema
Week 3-4    ████ Provider plugins (Sarvam, Gnani, OpenAI, Deepgram)
Week 5      ████ Pipecat integration + WebSocket bridge
Week 6      ████ Telephony: Exotel + Twilio
Week 7      ████ Basic dashboard (create agent, test call)
Week 8      ████ DPDP Consent Gateway v1
Week 9      ████ Transcripts + session history
Week 10     ████ NVIDIA Riva provider
Week 11-12  ████ Testing + docs + public release
             ↑ PHASE 1 COMPLETE ↑
Month 2     ████ Full DPDP module + Cost analytics
Month 3     ████ Visual agent builder + PersonaPlex
Month 4     ████ Batch campaigns + language expansion
Month 5-6   ████ CRM integrations + RBAC/SSO
             ↑ PHASE 2 COMPLETE ↑
Month 7-9   ████ HIPAA + GDPR modules
Month 9-12  ████ Convox Cloud launch
Month 10+   ████ PersonaPlex Indic + SOC2
             ↑ PHASE 3 COMPLETE ↑
```

---

## Dependencies & Critical Path

### Blocking Dependencies

| Item | Blocked By | Mitigation |
|------|------------|------------|
| Sarvam STT integration | Sarvam API key + docs access | Apply for early access immediately |
| Gnani Vachana TTS | Gnani API key | Contact via gnani.ai — they actively partner |
| NVIDIA Riva (Hindi) | NVIDIA API key or local GPU | Use NGC API first; local GPU for Phase 3 |
| PersonaPlex | NVIDIA developer access | Join waitlist; use existing Pipecat NVIDIA integration |
| Exotel telephony | Exotel account + India phone number | Register as India business entity |
| DPDP Consent Manager | Legal review | Get template consent language from DPDP-specialist lawyer |
| IndiaAI GPU subsidies | IndiaAI Mission application | Start application in Phase 1; takes 60-90 days |

### Non-Blocking (Can Parallel-Track)

- Frontend development and backend API can be built in parallel
- Provider plugins are independent of each other
- HIPAA and GDPR modules are independent
- Analytics and compliance modules are independent

---

## Decisions Made

| # | Question | Decision |
|---|----------|----------|
| 1 | Repo name | `convox-ai/convox` monorepo |
| 2 | License | Apache 2.0 for everything (including compliance) |
| 3 | Compliance boundaries | Fully open source; monetize via Cloud + enterprise support |
| 4 | Python version | 3.12+ (faster async, good ecosystem support in 2026) |
| 5 | Database access | **asyncpg + dbmate** — no ORM; raw SQL in repository pattern |
| 6 | Frontend | **Vite + React** SPA — built to static `dist/`, served by FastAPI monolith |
| 7 | Backend language | **Python** — Pipecat is Python-native; see `python-vs-go.md` |

## Open Questions (Still Need Decisions)

1. **Audio recording**: Default on or off? (Privacy implications; storage cost)
2. **Telephony first**: Start with Exotel-first (India) or Twilio-first (easier dev setup)?
3. **GitHub org**: Create `convox-ai` org before Phase 1, or start under personal account?
