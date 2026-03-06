# Convox — System Architecture

> Last updated: February 2026
> Status: Pre-implementation planning

---

## Overview

Convox is a **self-hosted, open-source voice AI orchestration platform** built on top of Pipecat. It sits between enterprise applications and voice AI providers, adding the dashboard, compliance tooling, analytics, and workflow management that no existing open-source solution provides.

Think of it as three bounded layers:

```
┌──────────────────────────────────────────────────────────────┐
│                      CONVOX PLATFORM                          │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐ │
│  │  Dashboard  │  │  Core API   │  │  Compliance Engine   │ │
│  │   (Web UI)  │  │  (REST/WS)  │  │  (DPDP/HIPAA/GDPR)  │ │
│  └─────────────┘  └─────────────┘  └──────────────────────┘ │
│                                                              │
│  ┌──────────────────────────────────────────────────────────┐│
│  │              ORCHESTRATION LAYER (Pipecat)               ││
│  │    Agent Runtime · Pipeline Execution · Turn Taking      ││
│  └──────────────────────────────────────────────────────────┘│
│                                                              │
│  ┌──────────────────────────────────────────────────────────┐│
│  │                   PROVIDER PLUGIN LAYER                  ││
│  │  STT · LLM · TTS · Telephony (all swappable)            ││
│  └──────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────┘
```

---

## Core Subsystems

### 1. Orchestration Layer (Pipecat Core)

Convox wraps Pipecat — it does NOT re-implement a voice pipeline. Pipecat handles:
- Audio frame streaming (VAD, buffering, chunking)
- Pipeline construction (frames flow through a series of processors)
- Turn detection / end-of-speech detection
- Transport abstraction (WebSocket, WebRTC, SIP)

What Convox adds ON TOP of Pipecat:
- Lifecycle management (start/stop/monitor agents via API)
- Multi-agent process orchestration (spawn N agents concurrently)
- Config-driven pipeline assembly (no hardcoded pipelines)
- Persistent state and session tracking per call

### 2. Provider Plugin Layer

Each provider is an isolated plugin implementing a standard interface. Providers are selected at agent-config time — never hardcoded.

#### Provider Categories

| Category | Providers (MVP) | Providers (Later) |
|----------|-----------------|-------------------|
| **STT** | Sarvam STT, NVIDIA Riva ASR, OpenAI Whisper, Deepgram | Google Speech, Azure Speech |
| **LLM** | OpenAI GPT-4o, Anthropic Claude, Sarvam Saarika | NVIDIA Nemotron, BharatGen Param2 |
| **TTS** | Gnani Vachana, NVIDIA Riva TTS, ElevenLabs, OpenAI TTS | Smallest.ai, Azure TTS |
| **Telephony** | Exotel, Twilio | Vonage, Plivo, SIP generic |
| **Full-duplex** | — (Phase 2) | NVIDIA PersonaPlex |

#### Plugin Interface Contract (conceptual)

Every STT provider exposes:
- `transcribe(audio_frame) → TranscriptFragment`
- `stream_start() / stream_stop()`
- `get_languages() → [str]`
- `get_cost_per_second() → float`

Every TTS provider exposes:
- `synthesize(text, voice_config) → AudioFrame`
- `stream_synthesize(text_stream) → AudioFrameStream`
- `get_voices() → [VoiceConfig]`
- `get_cost_per_char() → float`

Every LLM provider exposes:
- `chat(messages, system_prompt) → Message`
- `stream_chat(messages) → MessageStream`
- `get_cost_per_token() → float`

### 3. Core API Server

The central nervous system of Convox. All control-plane operations go through here.

```
Core API
├── /agents          → CRUD for agent definitions
├── /sessions        → Call session lifecycle (start, stop, status)
├── /transcripts     → Stored transcripts, search
├── /analytics       → Per-call cost, latency, quality metrics
├── /providers       → Provider configuration and testing
├── /compliance      → Consent logs, audit exports, deletion requests
├── /webhooks        → Outbound event webhooks to customer systems
└── /ws              → WebSocket endpoints for real-time call audio
```

Responsibilities:
- Session state management (Redis-backed)
- Agent process spawning and lifecycle
- Cost tracking (per STT/LLM/TTS/telephony call)
- Transcript storage (Postgres)
- Webhook dispatch to customer systems
- Auth (API keys + JWT for dashboard)

### 4. Dashboard (Web UI)

Vite + React SPA (TypeScript, Tailwind, shadcn/ui). Built to static files, served by the FastAPI backend as a monolith.

Pages/views:
```
Dashboard
├── Overview             → Active calls, cost summary, error rate
├── Agents               → List, create, edit agent configs
│   └── Agent Builder    → Visual no-code pipeline builder (Phase 2)
├── Sessions             → Call history, live call monitor
│   └── Session Detail   → Transcript, cost breakdown, audio playback
├── Analytics            → Cost charts, latency heatmaps, provider perf
├── Providers            → Configure STT/LLM/TTS/telephony, test them
├── Compliance           → Consent logs, deletion requests, audit exports
│   └── DPDP Module      → Voice consent dashboard, breach log
├── Settings             → API keys, webhooks, RBAC, SSO config
└── Team                 → Users, roles, permissions
```

### 5. Compliance Engine

Pluggable modules. Each compliance standard is an independent module that can be enabled/disabled per deployment. They hook into call lifecycle events.

```
Compliance Engine
├── Module: DPDP (India)
│   ├── ConsentGateway       → Plays in-call consent prompt, captures response
│   ├── ConsentStore         → Persists consent events (timestamped, signed)
│   ├── RetentionEngine      → Enforces configurable data retention TTLs
│   ├── DeletionEngine       → Handles right-to-erasure requests
│   ├── BreachNotifier       → 72-hour notification workflow
│   └── AuditExporter        → Generates compliance reports
│
├── Module: HIPAA (Phase 2)
│   ├── PHIHandler           → Identifies and redacts PHI in transcripts
│   ├── BAAManager           → BAA signature tracking
│   └── AccessAuditLog       → Detailed access logging for PHI
│
└── Module: GDPR (Phase 3)
    ├── ConsentManager       → GDPR-flavored consent (different from DPDP)
    ├── DataPortability      → Export all user data on request
    └── TransferMechanism    → Cross-border transfer documentation
```

---

## Data Flow: Inbound Voice Call

```
1. TELEPHONY ADAPTER
   Exotel/Twilio webhook fires → Core API /sessions/inbound
                                           │
2. SESSION INIT                            ▼
   Core API creates session record, spawns Pipecat agent process
                                           │
3. COMPLIANCE PRE-CALL                     ▼
   If DPDP enabled: ConsentGateway plays consent prompt
   User responds → consent event stored in ConsentStore
                                           │
4. AUDIO PIPELINE (Pipecat)               ▼
   Audio stream → STT Provider → Transcript fragment
   Transcript → LLM Provider → Response text
   Response text → TTS Provider → Audio stream
   Audio stream → back to caller via telephony adapter
                                           │
5. COST TRACKING (per frame)               ▼
   Each STT/LLM/TTS call → CostEvent emitted
   Core API aggregates → session cost record
                                           │
6. TRANSCRIPT STORAGE                      ▼
   Each turn stored: speaker, text, timestamp, confidence, cost
                                           │
7. SESSION END                             ▼
   Call terminates → session closed
   Retention policy applied
   Webhooks dispatched (transcript, cost summary, etc.)
```

---

## Data Flow: Outbound Campaign Call

```
1. CAMPAIGN API
   POST /campaigns with call list + agent config
                     │
2. SCHEDULER         ▼
   Rate-limited outbound dialing (respects DNC, time windows)
   Each call → same pipeline as inbound from step 2 above
                     │
3. BATCH ANALYTICS   ▼
   Campaign-level: completion rate, conversion, cost/call, avg duration
```

---

## Data Flow: Full-Duplex (Phase 2 — PersonaPlex)

```
Instead of STT → LLM → TTS cascade:

Audio stream → PersonaPlex (single model, GPU)
                     │
                     ├── Listens AND speaks simultaneously
                     ├── Native interruption handling
                     ├── Backchanneling ("mm-hmm", "I see")
                     └── 70ms response latency (vs 500-2000ms cascade)

Note: Cost tracking + compliance hooks still wrap the PersonaPlex call.
The provider interface hides the implementation difference.
```

---

## Deployment Architecture

### Single-Node (Default — Docker Compose)

```
docker-compose.yml defines:

┌─────────────────────────────────────────────┐
│              HOST MACHINE                   │
│                                             │
│  ┌─────────────┐  ┌───────────────────────┐ │
│  │  Caddy      │  │  Convox App           │ │
│  │  (reverse   │  │  (FastAPI :8000)      │ │
│  │   proxy,    │  │  serves React SPA     │ │
│  │   auto-TLS) │  │  + API + WebSocket    │ │
│  │  :80/:443   │  └───────────────────────┘ │
│  └─────────────┘                            │
│  ┌─────────────┐  ┌───────────────────────┐ │
│  │  Postgres   │  │  Redis                │ │
│  │  :5432      │  │  :6379                │ │
│  └─────────────┘  └───────────────────────┘ │
│  ┌───────────────────────────────────────┐  │
│  │  MinIO (optional, audio storage)      │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

### Multi-Node (Enterprise / High Volume)

```
                        Load Balancer
                             │
                ┌────────────┼────────────┐
                ▼            ▼            ▼
           API Node      API Node     API Node
                │
         ┌──────┴──────┐
         ▼             ▼
    Worker Pool    Worker Pool      ← Pipecat agent workers
    (voice calls)  (async jobs)
         │
    ┌────┴────┐
    ▼         ▼
 Postgres   Redis Cluster
 (primary + (session state,
  replicas)  call queues)
         │
    Object Store (S3 / MinIO)
    (audio recordings, exports)
```

---

## Database Schema (Conceptual)

### Core Tables

```
agents
  id, name, description, config_json, created_at, updated_at, team_id

sessions
  id, agent_id, direction (inbound/outbound), status, started_at, ended_at,
  caller_number, telephony_provider, cost_usd_total, team_id

transcripts
  id, session_id, turn_index, speaker (user/agent), text, timestamp_ms,
  stt_provider, stt_confidence, stt_cost_usd

cost_events
  id, session_id, event_type (stt/llm/tts/telephony), provider, amount_usd,
  tokens_or_chars_or_seconds, timestamp

consent_events (DPDP module)
  id, session_id, phone_number_hash, purpose, consent_given (bool),
  language, audio_ref, timestamp, expiry

audit_log
  id, actor_type (user/system), actor_id, action, resource_type,
  resource_id, details_json, timestamp

deletion_requests
  id, subject_phone_hash, requested_at, completed_at, status, records_deleted_count
```

---

## Security Architecture

- **Data in transit**: TLS everywhere (Caddy terminates, auto-TLS)
- **Data at rest**: Postgres encrypted volumes (customer-managed keys optional)
- **Audio recordings**: Encrypted at rest in object store; configurable retention TTL
- **PII handling**: Phone numbers stored as bcrypt hashes in consent/audit tables (never plaintext in logs)
- **API auth**: API keys (HMAC-SHA256 signed) for programmatic access; JWT (short-lived) for dashboard
- **RBAC roles**: Owner, Admin, Developer, Analyst, Read-only
- **Multi-tenant isolation**: All queries scoped by `team_id`; enforced at repository layer (every query includes team_id)

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Build on Pipecat vs custom runtime | **Pipecat** | Don't reinvent the pipeline engine; Pipecat has NVIDIA integration, active community, proven in production |
| API language | **Python (FastAPI)** | Pipecat is Python; same ecosystem; strong async support for concurrent calls |
| Frontend framework | **Vite + React** | Pure SPA (no SSR needed for dashboard); React ecosystem (shadcn/ui, React Flow for visual builder) |
| Primary DB | **PostgreSQL** | Reliable, ACID, JSON support for agent configs, good query capabilities for analytics |
| Session state | **Redis** | Sub-millisecond reads for live call state; pub/sub for real-time call events |
| Audio storage | **MinIO (self-hosted S3)** | Keeps audio data within the customer's infrastructure; S3-compatible API |
| Task queue | **asyncio tasks + Redis** | Background scheduled tasks; Celery only if needed at scale |
| Compliance as modules | **Plugin architecture** | Customers only pay the complexity cost for what they enable; each module = new market |
| Telephony abstraction | **Adapter pattern** | Swap Exotel for Twilio without changing any pipeline code |
