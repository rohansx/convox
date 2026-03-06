# Convox — Project Plan

> Last updated: February 2026
> Status: Pre-implementation planning

---

## What We're Building

**Convox** is an open-source voice AI orchestration platform. Self-hosted. Provider-neutral. Compliance-native.

It bridges the gap between voice AI frameworks (Pipecat) and enterprise requirements (dashboard, analytics, compliance modules). The Supabase analogy: Pipecat is to Convox as PostgreSQL is to Supabase — we add the layer that makes it enterprise-deployable.

See [architecture.md](./architecture.md) for system design, [tech-specs.md](./tech-specs.md) for technology choices, and [phases.md](./phases.md) for detailed phase breakdowns.

---

## The One-Sentence North Star

> **An Indian BFSI dev team should be able to ship DPDP-compliant Hindi voice agents in one afternoon.**

Everything in Phase 1 is measured against this. If it doesn't serve this goal, it waits.

---

## Immediate Priorities (Before Writing a Line of Code)

### 1. External Access / API Keys (Get These First)

| Service | Action | Why Urgent |
|---------|--------|------------|
| **Sarvam AI** | Apply at sarvam.ai for API access | Core STT/LLM provider for India stack |
| **Gnani.ai** | Contact sales@gnani.ai | Core Vachana TTS for Hindi |
| **NVIDIA NGC** | Register at ngc.nvidia.com | Riva ASR/TTS API; PersonaPlex access |
| **Deepgram** | Sign up at deepgram.com | Fallback STT; easiest to integrate first |
| **Exotel** | Register at exotel.com | India telephony; requires business registration |
| **Twilio** | Already accessible; sign up | Global telephony; easier for early dev |
| **IndiaAI Mission** | Check i4c.gov.in for GPU subsidy application | 60-90 day process; start now |

### 2. Legal / Compliance Prep

| Item | Action |
|------|--------|
| DPDP consent language templates | Get lawyer review of consent prompt text (Hindi + English) |
| Open source license decision | Confirm Apache 2.0 for all core features |
| Company incorporation | If targeting Indian enterprises: India entity needed for Exotel + DPDP Consent Manager registration |

### 3. Repository Setup

| Item | Decision |
|------|----------|
| GitHub org | Create `convox-ai` org (or `convox` if available) |
| Repo name | `convox` — single monorepo (frontend + backend + docs) |
| Branch strategy | `main` (stable), `dev` (integration), feature branches |
| CI/CD | GitHub Actions: lint + test on PR; Docker build on merge to main |
| Issue labels | `phase-1`, `phase-2`, `phase-3`, `provider:sarvam`, `compliance:dpdp`, `good-first-issue` |

---

## Project Structure (Monorepo)

```
convox/                         ← root of monorepo
├── docs/                       ← planning docs
│   ├── architecture.md
│   ├── tech-specs.md
│   ├── phases.md
│   ├── project-plan.md
│   ├── project-structure.md    ← full structure + Makefile + Docker
│   └── python-vs-go.md
│
├── api/                        ← Python backend (FastAPI + Pipecat)
│   ├── cmd/                    ← CLI entrypoints
│   ├── convox/                 ← main Python package
│   ├── migrations/             ← dbmate SQL migrations
│   ├── tests/
│   └── pyproject.toml
│
├── web/                        ← Vite + React dashboard
│   ├── src/
│   │   ├── routes/             ← page components
│   │   ├── components/         ← shadcn/ui + custom
│   │   └── lib/                ← api client, auth, realtime
│   ├── index.html
│   ├── vite.config.ts
│   └── package.json
│
├── Makefile                    ← `make up`, `make db-up`, `make api-dev`, etc.
├── Dockerfile                  ← multi-stage (frontend → backend → runtime)
├── docker-compose.yml          ← postgres + redis + app (monolith)
├── docker-compose.dev.yml      ← dev overrides (hot reload)
├── docker-entrypoint.sh        ← runs migrations then starts server
├── Caddyfile                   ← reverse proxy (auto-TLS)
├── .env.example                ← all env vars documented
├── README.md
└── LICENSE                     ← Apache 2.0
```

---

## Development Workflow

### Local Development Setup

Target: Any developer should be running locally in under 15 minutes.

```
1. git clone https://github.com/convox-ai/convox
2. cp .env.example .env && fill in API keys
3. make install
4. make up
5. Open http://localhost:5173 (frontend) / API on :8000
6. Create an agent, click Test Call
```

Dev runs with:
- Hot reload on backend (uvicorn --reload on :8000)
- Hot reload on frontend (Vite dev server on :5173)
- Real Postgres + Redis via Docker
- No SSL (plain HTTP for local)

### Testing Strategy

```
Unit tests       → Pure function tests; no DB, no network
                   Examples: provider cost calculation, consent prompt logic,
                   agent config validation

Integration      → Real DB + Redis; mocked external providers
tests              Examples: session lifecycle, DPDP consent flow,
                   cost aggregation

E2E tests        → Full stack; real providers (Twilio test credentials)
(Phase 2)          Examples: make a test call, verify transcript stored,
                   verify consent logged
```

Coverage targets:
- Core API routes: 80% line coverage
- Compliance engine: 90% line coverage (high-stakes code)
- Provider plugins: 70% (hard to mock well; focus on integration tests)

### Code Review Rules

- No PR merged without at least one reviewer approval
- All tests must pass
- No new `# type: ignore` without comment explaining why
- No hardcoded secrets (enforced by pre-commit hook via `detect-secrets`)
- DB migrations: always include rollback migration

---

## Key Technical Decisions (Documented Here)

### Decision 1: Build on Pipecat, Don't Fork

**Chosen**: Use Pipecat as a library dependency, contribute back upstream.

**Rationale**: Pipecat already has NVIDIA integration, active development, and production usage. Forking or reimplementing wastes months. Convox value is in the platform layer on top, not the pipeline engine.

**Implication**: We must track Pipecat releases and maintain compatibility. Pin to a minor version, upgrade deliberately.

### Decision 2: Python for Backend

**Chosen**: Python 3.11+ with FastAPI

**Rationale**: Pipecat is Python. All voice AI SDKs (Sarvam, Gnani, NVIDIA Riva, Deepgram, etc.) have Python clients. Async FastAPI handles concurrent calls well. Strong typing via Pydantic.

**Alternative considered**: Go (faster, better concurrency) — rejected because voice AI ecosystem is Python-first; too much glue code to write.

### Decision 3: Vite + React for Dashboard

**Chosen**: Vite + React 19 + TypeScript + Tailwind + shadcn/ui

**Rationale**: Dashboard is a pure SPA behind auth — zero SSR needed. Vite gives fast HMR and clean builds with no framework overhead. React ecosystem wins for dashboard needs: shadcn/ui (component library), React Flow (visual pipeline builder in Phase 2), Recharts (analytics). Monolith pattern: `vite build` → `dist/` → FastAPI serves static files.

**Alternatives considered**:
- Next.js — rejected: SSR/ISR features are never used; adds unnecessary complexity and requires Node runtime for serving
- SvelteKit — rejected: smaller component ecosystem hurts dashboard velocity; React Flow (visual builder) is React-only

### Decision 4: Self-Hosted First, Cloud Later

**Chosen**: Phase 1 is 100% self-hosted. Convox Cloud comes in Phase 3.

**Rationale**: The core value prop is self-hosting (for compliance, cost). Building managed cloud first contradicts the positioning. Open source community builds trust and distribution that makes Cloud valuable later.

### Decision 5: PostgreSQL + Redis + asyncpg + dbmate (No ORM)

**Chosen**: Postgres for all persistent data; Redis for session state and rate limiting. asyncpg for database access (raw SQL, repository pattern). dbmate for migrations (plain SQL files).

**Rationale**: Boring technology is good technology. asyncpg is 3-5x faster than SQLAlchemy async. Raw SQL in repository pattern is explicit — you see the exact query. dbmate migrations are dead simple (just SQL). This is the exact pattern Illuminate uses successfully (pgx + dbmate in Go; we use asyncpg + dbmate in Python).

**Alternatives rejected**:
- SQLAlchemy ORM + Alembic: more boilerplate, slower, Alembic autogenerate is fragile, another abstraction layer between you and the database
- Prisma/Drizzle: wrong language ecosystem (JS/TS)

**Future**: Consider TimescaleDB extension for analytics if query performance degrades at scale.

### Decision 6: Compliance Modules as Plugins (Not Baked In)

**Chosen**: Each compliance standard is an optional, independently activatable module.

**Rationale**: A US company doesn't want DPDP code running. A dev testing locally doesn't need any compliance overhead. Making modules optional reduces cognitive load and lets us build them independently. Each new module = new geographic market.

### Decision 7: Apache 2.0 License (Everything Open)

**Chosen**: Apache 2.0 for all code including compliance modules.

**Rationale**: Maximum community trust and adoption. Monetization comes from Convox Cloud (hosting) and Enterprise contracts (support, SLA, consulting) — not from restricting the software. This is the Supabase/PostHog/Mattermost model.

**Implication**: Competitors can use our compliance modules. That's okay — network effects and execution speed matter more than code secrecy.

---

## Risk Register and Mitigations

### R1: Solo Founder Execution Risk
- **Severity**: Very High
- **Mitigation**:
  - Scope Phase 1 ruthlessly — exactly the features listed, nothing more
  - Use OSS community to extend the provider list (good-first-issue tagging)
  - Find a technical co-founder or first engineer before Phase 2
  - Don't build what can be delegated: use shadcn/ui components, not custom design system; use asyncio tasks, not a custom job queue

### R2: Bolna Is Better Resourced ($6.3M, YC)
- **Severity**: High
- **Mitigation**:
  - Different buyer: Bolna targets SMEs; Convox targets enterprises needing control
  - Open source changes the sales motion (pull, not push)
  - Bolna has no NVIDIA integration, no DPDP compliance module, no self-hosted option
  - Ship MVP before Bolna adds these features (probably 6-12 months window)

### R3: Pipecat Cloud Adds Dashboard
- **Severity**: Medium
- **Mitigation**:
  - Pipecat Cloud will never offer self-hosting — that's structural, not tactical
  - DPDP/HIPAA compliance is a major pivot for Daily.co to make
  - Convox can be a featured project in the Pipecat ecosystem (mutual interest)

### R4: API Key Delays (Sarvam, Gnani, Exotel)
- **Severity**: Medium
- **Mitigation**:
  - Start provider applications TODAY, before any code is written
  - MVP can use Deepgram (STT) + OpenAI (LLM + TTS) as fallback while waiting
  - Use Twilio instead of Exotel for early development
  - Don't gate the entire MVP on one provider

### R5: DPDP Compliance Legal Risk
- **Severity**: Medium
- **Mitigation**:
  - Consult with a DPDP-specialist lawyer for consent language before launch
  - Add clear disclaimer: "Convox provides tooling; compliance is the deploying organization's responsibility"
  - Don't market as "DPDP certified" until legal review complete

### R6: PersonaPlex Stays English-Only
- **Severity**: Medium (low for MVP)
- **Mitigation**:
  - MVP doesn't use PersonaPlex at all — cascade works
  - If PersonaPlex never adds Indic: continue using cascade (Sarvam STT + LLM + Gnani TTS)
  - The platform value doesn't depend on PersonaPlex

---

## Go-to-Market: First 90 Days After Launch

### Week 1-2: GitHub Launch
- [ ] Public GitHub release with full README
- [ ] Demo video: "Zero to Hindi voice call in 10 minutes"
- [ ] Post on Hacker News (Show HN)
- [ ] Post on Reddit (r/MachineLearning, r/India, r/artificial)
- [ ] Tweet thread: architecture, NVIDIA integration, DPDP compliance angle
- [ ] LinkedIn post targeting Indian enterprise dev community
- [ ] Pipecat Discord: announce as official ecosystem project

### Week 3-4: Developer Community
- [ ] Write "self-hosted alternative to Vapi" blog post
- [ ] Write "DPDP-compliant voice AI" blog post (India-specific)
- [ ] Submit to NVIDIA Developer Blog (Riva + PersonaPlex integration story)
- [ ] Reach out to 5 Pipecat power users directly
- [ ] Create `good-first-issue` issues to attract contributors

### Month 2-3: Enterprise Pilots
- [ ] Identify 5 Indian BFSI companies from LinkedIn (HDFC, ICICI, Axis, Bajaj, etc.)
- [ ] Reach out directly to VoiceBot/CX teams
- [ ] Offer: free forward-deployed integration for first 3 enterprises
- [ ] Join iSPIRT / NASSCOM / IBA fintech events
- [ ] Get 1 reference customer for Convox Cloud waitlist

### Success Metrics (90 days post-launch)
- [ ] 200+ GitHub stars
- [ ] 50+ Discord/Slack community members
- [ ] 10+ self-hosted deployments (tracked via optional telemetry)
- [ ] 3+ enterprise pilots in progress
- [ ] 1+ external PR merged from community contributor

---

## Resource Requirements

### Phase 1 (Solo Founder)
- 1 developer (you) full-time for 8-12 weeks
- External costs:
  - Cloud hosting for demo instance: ~$100/month (small VPS)
  - API credits for testing: ~$200 total (Deepgram, OpenAI, Twilio sandbox)
  - Domain: convox.ai or convoxhq.com (~$20/year)
  - Legal: DPDP consent template review (~$500-1000 one-time)

### Phase 2 (Team of 2-3)
- 1 backend engineer (hire or co-founder)
- 1 frontend/fullstack engineer
- Optional: 1 forward-deployed engineer for enterprise pilots
- Monthly cloud costs: ~$500/month (demo + staging instances)

### Phase 3 (Team of 5-8)
- 2 backend engineers
- 1 frontend engineer
- 1 DevOps/infrastructure engineer (for Convox Cloud)
- 1 enterprise sales / forward-deployed engineer
- Legal + compliance consultant (SOC2, HIPAA)

---

## Definition of Done: Phase 1 Checklist

### Code
- [ ] All Phase 1 features implemented per [phases.md](./phases.md)
- [ ] Test coverage: Core API routes 80%, Compliance engine 90%
- [ ] No known security vulnerabilities (run `pip-audit`, `npm audit`)
- [ ] All environment variables documented in `.env.example`
- [ ] Docker Compose works on fresh Ubuntu 22.04 machine

### Documentation
- [ ] README: Installation, quickstart, first call in < 10 minutes
- [ ] Agent configuration schema documented (all fields, examples)
- [ ] Provider plugin guide: how to add a new STT/LLM/TTS provider
- [ ] DPDP compliance guide: what it does, how to enable, legal disclaimer
- [ ] API reference (auto-generated from FastAPI OpenAPI spec)
- [ ] CONTRIBUTING.md: setup, test, PR process

### Release
- [ ] GitHub repo public with Apache 2.0 LICENSE
- [ ] Docker Hub / GHCR images published
- [ ] v0.1.0 tag with release notes
- [ ] Demo video (5 minutes max: setup → first Hindi call)
- [ ] HN post drafted and ready
