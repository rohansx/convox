<p align="center">
  <img src="https://img.shields.io/badge/status-pre--alpha-orange" alt="Status" />
  <img src="https://img.shields.io/badge/license-Apache%202.0-blue" alt="License" />
  <img src="https://img.shields.io/badge/python-3.12+-yellow" alt="Python" />
  <img src="https://img.shields.io/badge/react-19-61dafb" alt="React" />
</p>

<h1 align="center">convox</h1>
<p align="center"><strong>Open-source voice AI orchestration for India</strong></p>
<p align="center">Build production voice agents in 22+ Indian languages. Plug in any STT, LLM, or TTS provider. Ship compliant, cost-tracked conversations — no vendor lock-in.</p>

<p align="center">
  <a href="https://convox.ai">Website</a> · <a href="https://convox.ai/demo">Live Demo</a> · <a href="docs/architecture.md">Architecture</a> · <a href="docs/tech-specs.md">Tech Specs</a>
</p>

---

## What is Convox?

Convox is a **self-hosted voice AI orchestration platform** built on [Pipecat](https://github.com/pipecat-ai/pipecat). It sits between your application and voice AI providers, adding:

- **Provider orchestration** — swap STT, TTS, LLM, and telephony providers without changing pipeline code
- **Indian language support** — first-class support for Hindi, Tamil, Telugu, Bengali, Marathi, Kannada, and all 22 scheduled languages
- **Cost tracking** — per-session, per-provider cost attribution for every conversation
- **Compliance engine** — DPDP Act compliant with consent tracking, audit logging, and data retention policies
- **Dashboard** — monitor active calls, review transcripts, analyze costs, and configure agents

```
┌──────────────────────────────────────────────────────────┐
│                     CONVOX PLATFORM                       │
│                                                          │
│  ┌───────────┐  ┌───────────┐  ┌──────────────────────┐ │
│  │ Dashboard │  │ Core API  │  │ Compliance Engine    │ │
│  │  (React)  │  │ (FastAPI) │  │ (DPDP / HIPAA)       │ │
│  └───────────┘  └───────────┘  └──────────────────────┘ │
│                                                          │
│  ┌──────────────────────────────────────────────────────┐│
│  │           ORCHESTRATION LAYER (Pipecat)              ││
│  │   Agent Runtime · Pipeline Execution · Turn Taking   ││
│  └──────────────────────────────────────────────────────┘│
│                                                          │
│  ┌──────────────────────────────────────────────────────┐│
│  │              PROVIDER PLUGIN LAYER                   ││
│  │   STT · LLM · TTS · Telephony (all swappable)       ││
│  └──────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

## Supported Providers

| Category | Providers |
|----------|-----------|
| **STT** | Sarvam AI, Deepgram, NVIDIA Riva, Azure Speech, OpenAI Whisper |
| **LLM** | OpenAI, Anthropic Claude, Sarvam Saarika, Groq |
| **TTS** | Sarvam AI, Gnani Vachana, ElevenLabs, Azure Neural |
| **Telephony** | Exotel, Twilio |

Every provider is a pluggable module implementing a standard interface — add your own with a single Python class.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Voice Pipeline | [Pipecat](https://github.com/pipecat-ai/pipecat) |
| API Backend | Python 3.12+ / FastAPI / asyncpg (no ORM) |
| Database | PostgreSQL 17 / Redis 7 |
| Migrations | dbmate (plain SQL) |
| Frontend | Vite / React 19 / TypeScript / Tailwind v4 |
| Infrastructure | Docker Compose / single-container monolith |

## Project Structure

```
convox/
├── api/
│   ├── convox/              # Python package
│   │   ├── app.py           # FastAPI app factory
│   │   ├── config.py        # Pydantic settings (env vars)
│   │   ├── database/        # asyncpg + Redis connections
│   │   ├── handler/         # HTTP route handlers
│   │   ├── middleware/       # CORS, logging
│   │   ├── model/           # Pydantic schemas
│   │   ├── providers/       # STT/LLM/TTS/telephony plugins
│   │   ├── repository/      # Data access (raw SQL)
│   │   ├── service/         # Business logic
│   │   ├── compliance/      # DPDP compliance module
│   │   └── ws/              # WebSocket handlers
│   ├── migrations/          # dbmate SQL migrations (8 tables)
│   ├── tests/               # pytest suite
│   └── pyproject.toml       # Python deps (uv)
│
├── web/
│   ├── src/
│   │   ├── routes/          # Page components
│   │   ├── components/      # Reusable UI components
│   │   ├── lib/             # API client, utilities
│   │   └── types/           # TypeScript types
│   └── vite.config.ts
│
├── docs/                    # Architecture & tech specs
├── docker-compose.yml       # Full stack: postgres + redis + app
├── Dockerfile               # Multi-stage build
└── Makefile                 # Dev commands
```

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.12+ and [uv](https://github.com/astral-sh/uv)
- Node.js 20+ (for frontend dev)

### 1. Clone and configure

```bash
git clone https://github.com/rohansx/convox.git
cd convox
cp .env.example .env
# Edit .env with your provider API keys
```

### 2. Start infrastructure

```bash
make db-up      # Start PostgreSQL
make db-migrate # Run all migrations
```

### 3. Run the API

```bash
cd api
uv sync --all-extras
uv run uvicorn convox.app:app --reload --port 8000
```

### 4. Run the frontend

```bash
cd web
bun install
bun run dev
```

### 5. Or use Docker Compose (everything at once)

```bash
docker compose up --build
```

The dashboard will be available at `http://localhost:5173` and the API at `http://localhost:8000`.

## API Overview

```
/health                           → Health check
/v1/agents                        → CRUD for agent definitions
/v1/sessions                      → Call session lifecycle
/v1/sessions/{id}/transcript      → Stored transcripts
/v1/analytics/overview            → Cost & latency metrics
/v1/providers                     → Provider configuration
/v1/compliance/dpdp/consents      → DPDP consent records
/ws/call                          → Real-time audio WebSocket
```

See [docs/tech-specs.md](docs/tech-specs.md) for the complete API reference.

## How a Call Flows

```
Inbound Call (Exotel/Twilio)
    │
    ▼
Session Created → Compliance check (DPDP consent)
    │
    ▼
Audio Stream → STT (Sarvam/Deepgram) → Transcript
    │
    ▼
Transcript → LLM (Claude/GPT-4o) → Response
    │
    ▼
Response → TTS (ElevenLabs/Gnani) → Audio
    │
    ▼
Audio → Back to caller
    │
    ▼
Log: transcript, latency, cost per provider
```

Every step is observable, swappable, and cost-tracked.

## Indian Language Support

Convox is built India-first, not India-as-afterthought:

| Language | Code | STT | TTS |
|----------|------|-----|-----|
| Hindi | hi | Sarvam, Deepgram | Sarvam, Gnani |
| Tamil | ta | Sarvam | Sarvam, Gnani |
| Telugu | te | Sarvam | Sarvam, Gnani |
| Bengali | bn | Sarvam | Sarvam, Gnani |
| Marathi | mr | Sarvam | Sarvam, Gnani |
| Kannada | kn | Sarvam | Sarvam, Gnani |
| Gujarati | gu | Sarvam | Sarvam |
| Malayalam | ml | Sarvam | Sarvam |
| Punjabi | pa | Sarvam | Sarvam |
| Odia | or | Sarvam | Sarvam |
| Assamese | as | Sarvam | Sarvam |
| English | en | All providers | All providers |

## Compliance

The compliance engine is modular — enable only what you need:

- **DPDP Act (India)** — voice consent capture, consent storage, configurable retention TTLs, right-to-erasure, 72-hour breach notification, audit export
- **HIPAA** — planned
- **GDPR** — planned

## Development

```bash
# Run tests
cd api && uv run pytest

# Lint
cd api && uv run ruff check .

# Type check frontend
cd web && npx tsc --noEmit

# Build frontend for production
cd web && bun run build
```

## Contributing

Convox is Apache 2.0 licensed. We welcome contributions:

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/amazing-thing`)
3. Commit your changes
4. Push to your branch
5. Open a Pull Request

## License

[Apache License 2.0](LICENSE)

---

<p align="center"><strong>Built for India. Open to the world.</strong></p>
