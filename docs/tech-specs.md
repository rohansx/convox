# Convox — Technical Specifications

> Last updated: February 2026
> Status: Pre-implementation planning

---

## Stack Overview

| Layer | Technology | Version / Notes |
|-------|-----------|-----------------|
| **Voice Pipeline** | Pipecat | Latest; core dependency |
| **API Backend** | Python + FastAPI | Python 3.12+; async throughout |
| **Frontend** | Vite + React + TypeScript | Pure SPA; no SSR framework needed |
| **Component Library** | shadcn/ui + Radix + Tailwind v4 | Polished dashboard components out of the box |
| **Primary Database** | PostgreSQL | v17+; via asyncpg (raw SQL, no ORM) |
| **Migrations** | dbmate | Plain SQL migrations (same as Illuminate) |
| **Session Store / Cache** | Redis | v7+; rate limiting, session state, pub/sub |
| **Audio/Object Store** | MinIO | S3-compatible; self-hosted |
| **Reverse Proxy** | Caddy | Automatic TLS, zero-config (same as Illuminate) |
| **Containerization** | Docker + Docker Compose | Single-command deploy |
| **Testing** | pytest + pytest-asyncio | Unit + integration |
| **Linting** | Ruff + mypy | Python; tsc for frontend |
| **Package Manager (Python)** | uv | Fast, replaces pip/poetry |
| **Package Manager (Frontend)** | Bun | Fast, replaces npm (same as Illuminate) |

---

## Python Backend: Detailed Specs

### Package Structure

```
api/
├── cmd/                       ← CLI entrypoints (like Illuminate's cmd/)
│   ├── server.py              ← Main server entrypoint
│   └── seed.py                ← Seed demo agents
│
├── convox/                    ← Main Python package
│   ├── __init__.py
│   ├── config.py              ← Pydantic Settings (env vars, like Illuminate's envconfig)
│   ├── app.py                 ← FastAPI app factory + route registration
│   │
│   ├── database/              ← DB + Redis (like Illuminate's database/)
│   │   ├── __init__.py
│   │   ├── postgres.py        ← asyncpg connection pool
│   │   └── redis.py           ← Redis client (optional, graceful degradation)
│   │
│   ├── crypto/                ← Auth utilities (like Illuminate's crypto/)
│   │   ├── __init__.py
│   │   ├── jwt.py             ← JWT signing + validation
│   │   └── encryption.py      ← AES encryption for stored API keys
│   │
│   ├── middleware/             ← HTTP middleware (like Illuminate's middleware/)
│   │   ├── __init__.py
│   │   ├── auth.py            ← API key + JWT validation
│   │   ├── cors.py            ← CORS configuration
│   │   ├── rate_limit.py      ← Redis-backed rate limiting
│   │   ├── team_scope.py      ← Multi-tenant query scoping
│   │   └── logger.py          ← Structured request logging
│   │
│   ├── model/                 ← Domain models / Pydantic schemas (like Illuminate's model/)
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   ├── session.py
│   │   ├── transcript.py
│   │   ├── cost.py
│   │   ├── consent.py
│   │   ├── user.py
│   │   └── audit.py
│   │
│   ├── repository/            ← Data access: raw SQL via asyncpg (like Illuminate's repository/)
│   │   ├── __init__.py
│   │   ├── agent_repo.py
│   │   ├── session_repo.py
│   │   ├── transcript_repo.py
│   │   ├── cost_repo.py
│   │   ├── consent_repo.py
│   │   ├── user_repo.py
│   │   └── audit_repo.py
│   │
│   ├── service/               ← Business logic (like Illuminate's service/)
│   │   ├── __init__.py
│   │   ├── agent_service.py
│   │   ├── session_service.py
│   │   ├── pipeline_service.py    ← Pipecat pipeline assembly from agent config
│   │   ├── cost_service.py
│   │   ├── analytics_service.py
│   │   ├── consent_service.py
│   │   ├── retention_service.py
│   │   ├── deletion_service.py
│   │   ├── webhook_service.py
│   │   ├── auth_service.py
│   │   └── scheduler.py          ← Background scheduled tasks
│   │
│   ├── handler/               ← HTTP route handlers (like Illuminate's handler/)
│   │   ├── __init__.py
│   │   ├── agents.py          ← /v1/agents
│   │   ├── sessions.py        ← /v1/sessions
│   │   ├── transcripts.py     ← /v1/transcripts
│   │   ├── analytics.py       ← /v1/analytics
│   │   ├── providers.py       ← /v1/providers
│   │   ├── compliance.py      ← /v1/compliance/dpdp
│   │   ├── webhooks.py        ← /v1/webhooks
│   │   ├── auth.py            ← /auth
│   │   ├── health.py          ← /health
│   │   └── inbound.py         ← Telephony inbound webhook
│   │
│   ├── ws/                    ← WebSocket handlers
│   │   ├── __init__.py
│   │   └── call.py            ← Real-time audio WebSocket for test calls
│   │
│   ├── providers/             ← Voice AI provider plugins
│   │   ├── __init__.py
│   │   ├── base.py            ← Abstract base classes (STT, LLM, TTS, Telephony)
│   │   ├── registry.py        ← Provider registry (lookup by id)
│   │   ├── stt/
│   │   │   ├── __init__.py
│   │   │   ├── sarvam.py
│   │   │   ├── deepgram.py
│   │   │   ├── openai_whisper.py
│   │   │   └── nvidia_riva.py
│   │   ├── llm/
│   │   │   ├── __init__.py
│   │   │   ├── openai.py
│   │   │   ├── anthropic.py
│   │   │   └── sarvam_saarika.py
│   │   ├── tts/
│   │   │   ├── __init__.py
│   │   │   ├── gnani.py
│   │   │   ├── elevenlabs.py
│   │   │   ├── openai.py
│   │   │   └── nvidia_riva.py
│   │   └── telephony/
│   │       ├── __init__.py
│   │       ├── exotel.py
│   │       └── twilio.py
│   │
│   └── compliance/            ← Compliance modules (pluggable)
│       ├── __init__.py
│       ├── base.py            ← ComplianceModule abstract class
│       └── dpdp/
│           ├── __init__.py
│           ├── consent_gateway.py
│           ├── consent_store.py
│           ├── retention_engine.py
│           ├── deletion_engine.py
│           ├── breach_notifier.py
│           └── audit_exporter.py
│
├── migrations/                ← dbmate SQL migrations
│   ├── 20260225000001_create_users.sql
│   ├── 20260225000002_create_agents.sql
│   ├── 20260225000003_create_sessions.sql
│   ├── 20260225000004_create_transcripts.sql
│   ├── 20260225000005_create_cost_events.sql
│   ├── 20260225000006_create_consent_events.sql
│   ├── 20260225000007_create_audit_log.sql
│   └── 20260225000008_create_provider_configs.sql
│
├── db/
│   └── schema.sql             ← Schema dump (auto-generated by dbmate)
│
├── tests/
│   ├── conftest.py
│   ├── test_agents.py
│   ├── test_sessions.py
│   ├── test_compliance.py
│   └── test_providers.py
│
└── pyproject.toml             ← Python project config (uv)
```

### Key Dependencies

```
# Core
fastapi>=0.115
uvicorn[standard]>=0.30
pipecat-ai[daily,silero,deepgram,openai,elevenlabs]
pydantic>=2.7
pydantic-settings>=2.3

# Database (raw SQL, no ORM)
asyncpg>=0.29
redis[hiredis]>=5.0

# Auth
python-jose[cryptography]
passlib[bcrypt]

# Providers (installed per-need)
deepgram-sdk>=3.0
anthropic>=0.28
openai>=1.30
nvidia-pipecat>=0.1

# Telephony
twilio>=9.0
httpx>=0.27              # for Exotel REST calls

# Storage
boto3>=1.34              # S3/MinIO
minio>=7.2

# Observability
structlog>=24.0
sentry-sdk[fastapi]>=2.0
prometheus-fastapi-instrumentator>=7.0
```

---

## Database: asyncpg + dbmate (No ORM)

### Why No ORM

- asyncpg is 3-5x faster than SQLAlchemy async for the same queries
- Raw SQL in repository pattern is explicit — you see exactly what runs
- dbmate migrations are plain SQL files, not Python code
- Illuminate proves this pattern works at scale (28 repository files, zero ORM)

### Repository Pattern Example

```python
# repository/agent_repo.py (conceptual)

class AgentRepo:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get_by_id(self, agent_id: str, team_id: str) -> Agent | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM agents WHERE id = $1 AND team_id = $2",
            agent_id, team_id
        )
        return Agent(**dict(row)) if row else None

    async def create(self, agent: AgentCreate, team_id: str) -> Agent:
        row = await self.pool.fetchrow(
            """INSERT INTO agents (name, description, config, team_id)
               VALUES ($1, $2, $3, $4)
               RETURNING *""",
            agent.name, agent.description, agent.config, team_id
        )
        return Agent(**dict(row))

    async def list_by_team(self, team_id: str, limit: int = 50, offset: int = 0) -> list[Agent]:
        rows = await self.pool.fetch(
            """SELECT * FROM agents WHERE team_id = $1
               ORDER BY created_at DESC LIMIT $2 OFFSET $3""",
            team_id, limit, offset
        )
        return [Agent(**dict(row)) for row in rows]
```

### Migration Example

```sql
-- migrations/20260225000002_create_agents.sql

-- migrate:up
CREATE TABLE agents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    description TEXT,
    config      JSONB NOT NULL DEFAULT '{}',
    team_id     UUID NOT NULL REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agents_team_id ON agents(team_id);

-- migrate:down
DROP TABLE IF EXISTS agents;
```

---

## Provider Interface Specs

### STT Base Class

```python
# providers/base.py (conceptual)

class STTProvider(ABC):
    provider_id: str
    supported_languages: list[str]  # BCP-47: ["hi", "en", "hi-IN"]

    @abstractmethod
    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        language: str,
        sample_rate: int = 16000,
    ) -> AsyncIterator[TranscriptFragment]: ...

    @abstractmethod
    def cost_per_second(self) -> float: ...  # USD
```

### TTS Base Class

```python
class TTSProvider(ABC):
    provider_id: str
    supported_languages: list[str]
    voices: list[VoiceConfig]

    @abstractmethod
    async def synthesize_stream(
        self,
        text_stream: AsyncIterator[str],
        voice: VoiceConfig,
    ) -> AsyncIterator[bytes]: ...  # raw PCM audio

    @abstractmethod
    def cost_per_character(self) -> float: ...
```

### LLM Base Class

```python
class LLMProvider(ABC):
    provider_id: str

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[Message],
        system_prompt: str,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]: ...

    @abstractmethod
    def cost_per_1k_tokens(self) -> tuple[float, float]: ...  # (input, output)
```

### Telephony Base Class

```python
class TelephonyAdapter(ABC):
    provider_id: str

    @abstractmethod
    async def initiate_call(
        self,
        to_number: str,
        from_number: str,
        webhook_url: str,
    ) -> CallHandle: ...

    @abstractmethod
    async def end_call(self, call_handle: CallHandle) -> None: ...

    @abstractmethod
    def parse_inbound_webhook(self, payload: dict) -> InboundCallEvent: ...
```

---

## Frontend: Vite + React SPA

### Why Vite + React (Not Next.js, Not SvelteKit)

The dashboard is a **pure SPA behind auth** — zero SEO, zero SSR. Both Next.js and SvelteKit are overkill because their main value (server-side rendering) is never used.

| Factor | Vite + React | SvelteKit | Next.js |
|--------|-------------|-----------|---------|
| What you get | SPA, fast HMR | SPA + unused SSR framework | Full SSR you don't need |
| Bundle | ~45KB (React 19) | ~15KB (Svelte 5) | ~85KB+ |
| Component ecosystem | shadcn/ui, Radix, React Flow | Fewer polished libs | Same as React |
| Visual builder (Phase 2) | React Flow (best in class) | Svelte Flow (less mature) | Same as React |
| Monolith serving | `vite build` → `dist/` → FastAPI serves static | Same pattern | Needs Node runtime |

### Frontend Structure

```
web/
├── src/
│   ├── main.tsx                   ← App entrypoint
│   ├── App.tsx                    ← Router setup
│   ├── index.css                  ← Global styles + Tailwind
│   │
│   ├── routes/                    ← Page components (one per route)
│   │   ├── Landing.tsx            ← / (public landing page)
│   │   ├── Login.tsx              ← /login
│   │   ├── Overview.tsx           ← /app (dashboard home)
│   │   ├── AgentList.tsx          ← /app/agents
│   │   ├── AgentDetail.tsx        ← /app/agents/:id
│   │   ├── AgentNew.tsx           ← /app/agents/new
│   │   ├── SessionList.tsx        ← /app/sessions
│   │   ├── SessionDetail.tsx      ← /app/sessions/:id
│   │   ├── Analytics.tsx          ← /app/analytics
│   │   ├── Providers.tsx          ← /app/providers
│   │   ├── Compliance.tsx         ← /app/compliance
│   │   └── Settings.tsx           ← /app/settings
│   │
│   ├── components/                ← Reusable components
│   │   ├── ui/                    ← shadcn/ui base components
│   │   ├── layout/
│   │   │   ├── DashboardLayout.tsx    ← Sidebar + nav shell
│   │   │   ├── Sidebar.tsx
│   │   │   └── Nav.tsx
│   │   ├── agents/
│   │   │   ├── AgentCard.tsx
│   │   │   └── AgentForm.tsx
│   │   ├── sessions/
│   │   │   ├── SessionRow.tsx
│   │   │   ├── TranscriptView.tsx
│   │   │   └── CallPlayer.tsx     ← Audio playback + transcript overlay
│   │   ├── analytics/
│   │   │   ├── CostChart.tsx
│   │   │   └── LatencyChart.tsx
│   │   └── compliance/
│   │       └── ConsentLog.tsx
│   │
│   ├── lib/
│   │   ├── api.ts                 ← Typed API client (like Illuminate's api.ts)
│   │   ├── auth.ts                ← Auth state (token storage, refresh logic)
│   │   ├── realtime.ts            ← WebSocket client for live call audio
│   │   └── utils.ts               ← Formatters, helpers
│   │
│   └── types/                     ← TypeScript types (mirrors API schemas)
│       ├── agent.ts
│       ├── session.ts
│       └── index.ts
│
├── index.html                     ← Vite HTML entrypoint
├── vite.config.ts                 ← Vite config (env from parent dir)
├── tailwind.config.ts
├── tsconfig.json
├── components.json                ← shadcn/ui config
├── package.json
└── bun.lock
```

### Frontend Dependencies

```json
{
  "react": "^19",
  "react-dom": "^19",
  "react-router-dom": "^7",
  "typescript": "^5",
  "vite": "^6",
  "@vitejs/plugin-react": "^4",
  "tailwindcss": "^4",
  "@tanstack/react-query": "^5",
  "zustand": "^5",
  "recharts": "^2",
  "@xyflow/react": "^12",
  "@radix-ui/react-dialog": "*",
  "@radix-ui/react-dropdown-menu": "*",
  "@radix-ui/react-select": "*",
  "@radix-ui/react-tabs": "*",
  "class-variance-authority": "^0.7",
  "clsx": "^2",
  "tailwind-merge": "^2",
  "date-fns": "^3",
  "zod": "^3",
  "lucide-react": "^0.400"
}
```

### Routing (react-router-dom)

```tsx
// App.tsx (conceptual)
<Routes>
  {/* Public */}
  <Route path="/" element={<Landing />} />
  <Route path="/login" element={<Login />} />

  {/* Dashboard (auth required) */}
  <Route path="/app" element={<AuthGuard><DashboardLayout /></AuthGuard>}>
    <Route index element={<Overview />} />
    <Route path="agents" element={<AgentList />} />
    <Route path="agents/new" element={<AgentNew />} />
    <Route path="agents/:id" element={<AgentDetail />} />
    <Route path="sessions" element={<SessionList />} />
    <Route path="sessions/:id" element={<SessionDetail />} />
    <Route path="analytics" element={<Analytics />} />
    <Route path="providers" element={<Providers />} />
    <Route path="compliance" element={<Compliance />} />
    <Route path="settings" element={<Settings />} />
  </Route>
</Routes>
```

---

## Agent Configuration Schema

Agents are defined as JSON configs stored in Postgres (JSONB column). This is the schema:

```jsonc
{
  "id": "uuid",
  "name": "Hindi Collections Agent",
  "description": "Handles debt collection calls in Hindi",

  // Provider selections
  "stt": {
    "provider": "sarvam",
    "language": "hi-IN",
    "config": {}
  },
  "llm": {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "system_prompt": "You are a collections agent...",
    "temperature": 0.3,
    "max_tokens": 150
  },
  "tts": {
    "provider": "gnani",
    "voice": "priya-hi-IN",
    "config": {}
  },
  "telephony": {
    "provider": "exotel",
    "from_number": "+91XXXXXXXXXX"
  },

  // Pipeline behavior
  "vad": {
    "enabled": true,
    "silence_threshold_ms": 800
  },
  "interruption_handling": {
    "enabled": true,
    "mode": "restart"
  },

  // Compliance
  "compliance": {
    "dpdp": {
      "enabled": true,
      "consent_language": "hi",
      "consent_purpose": "debt_collection",
      "retention_days": 180
    }
  },

  // Webhooks
  "webhooks": {
    "on_call_end": "https://your-crm.com/hooks/call-complete",
    "on_transcript_ready": "https://your-crm.com/hooks/transcript"
  }
}
```

---

## API Endpoints: Complete Reference

### Agents

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/agents` | List all agents |
| POST | `/v1/agents` | Create agent |
| GET | `/v1/agents/{id}` | Get agent config |
| PUT | `/v1/agents/{id}` | Update agent config |
| DELETE | `/v1/agents/{id}` | Delete agent |
| POST | `/v1/agents/{id}/test` | Test agent (browser call) |

### Sessions

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/sessions` | List sessions (filterable by agent, date, status) |
| POST | `/v1/sessions` | Start outbound session |
| GET | `/v1/sessions/{id}` | Get session detail |
| DELETE | `/v1/sessions/{id}` | Force-end active session |
| GET | `/v1/sessions/{id}/transcript` | Get full transcript |
| GET | `/v1/sessions/{id}/cost` | Get cost breakdown |
| POST | `/v1/inbound/{agent_id}` | Telephony webhook (inbound call entry) |

### Analytics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/analytics/overview` | Summary stats (calls, cost, duration) |
| GET | `/v1/analytics/cost` | Cost breakdown by provider, time range |
| GET | `/v1/analytics/latency` | P50/P95/P99 latency by provider |
| GET | `/v1/analytics/providers` | Provider performance comparison |

### Compliance (DPDP)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/compliance/dpdp/consents` | List consent records |
| GET | `/v1/compliance/dpdp/consents/{session_id}` | Get consent for call |
| POST | `/v1/compliance/dpdp/deletion` | Submit erasure request |
| GET | `/v1/compliance/dpdp/deletion/{id}` | Deletion request status |
| GET | `/v1/compliance/dpdp/audit-export` | Export audit log (CSV/JSON) |

### Providers

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/providers` | List available + configured providers |
| POST | `/v1/providers/{id}/configure` | Set credentials for provider |
| POST | `/v1/providers/{id}/test` | Test provider connectivity |
| GET | `/v1/providers/{id}/pricing` | Get live pricing info |

---

## Environment Variables

```bash
# ─── Database ────────────────────────────────────────────
DATABASE_URL=postgres://convox:convox@localhost:5432/convox?sslmode=disable
CONVOX_DATABASE_URL=postgres://convox:convox@localhost:5432/convox?sslmode=disable

# ─── Cache ───────────────────────────────────────────────
CONVOX_REDIS_URL=redis://localhost:6379

# ─── Security ────────────────────────────────────────────
CONVOX_JWT_SECRET=change-me-to-random-64-hex-chars
CONVOX_ENCRYPT_KEY=change-me-to-random-64-hex-chars

# ─── Server ──────────────────────────────────────────────
CONVOX_PORT=8000
CONVOX_ENV=development
CONVOX_FRONTEND_URL=http://localhost:5173
CONVOX_BACKEND_URL=http://localhost:8000
CONVOX_COOKIE_DOMAIN=localhost

# ─── Provider API Keys (set the ones you use) ───────────
CONVOX_OPENAI_API_KEY=
CONVOX_ANTHROPIC_API_KEY=
CONVOX_DEEPGRAM_API_KEY=
CONVOX_ELEVENLABS_API_KEY=
CONVOX_SARVAM_API_KEY=
CONVOX_GNANI_API_KEY=
CONVOX_NVIDIA_API_KEY=

# ─── Telephony ───────────────────────────────────────────
CONVOX_TWILIO_ACCOUNT_SID=
CONVOX_TWILIO_AUTH_TOKEN=
CONVOX_EXOTEL_API_KEY=
CONVOX_EXOTEL_API_TOKEN=

# ─── Compliance ──────────────────────────────────────────
CONVOX_DPDP_ENABLED=false
CONVOX_DPDP_BREACH_NOTIFY_EMAIL=

# ─── Audio Storage ───────────────────────────────────────
CONVOX_MINIO_ENDPOINT=localhost:9000
CONVOX_MINIO_ACCESS_KEY=
CONVOX_MINIO_SECRET_KEY=
CONVOX_MINIO_BUCKET=convox-audio
```

---

## WebSocket Protocol (Real-time Audio)

Used for browser-based test calls and live call monitoring.

### Call Initiation (browser -> server)

```json
{
  "type": "start_call",
  "agent_id": "uuid",
  "audio_format": {
    "encoding": "pcm_s16le",
    "sample_rate": 16000,
    "channels": 1
  }
}
```

### Audio Streaming (bidirectional)

- Client -> Server: raw PCM audio bytes (binary frames)
- Server -> Client: raw PCM audio bytes (binary frames)

### Control Messages (text frames)

```json
{ "type": "transcript", "speaker": "user", "text": "...", "is_final": true }
{ "type": "transcript", "speaker": "agent", "text": "...", "is_final": false }
{ "type": "cost_update", "stt_usd": 0.001, "llm_usd": 0.003, "tts_usd": 0.002 }
{ "type": "end_call", "reason": "user_hangup" }
```

---

## Infrastructure: Monolith Docker Setup

### Dockerfile (Multi-Stage — same pattern as Illuminate)

```dockerfile
# Stage 1: Frontend build
FROM oven/bun:1 AS frontend
WORKDIR /app/web
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile
COPY web/ .
COPY .env.example ../.env
RUN bun run build

# Stage 2: Python backend deps
FROM python:3.12-slim AS backend
WORKDIR /app
RUN pip install uv
COPY api/pyproject.toml api/uv.lock ./api/
RUN cd api && uv sync --frozen --no-dev
COPY api/ ./api/

# Stage 3: Runtime (single container)
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y curl ca-certificates && \
    curl -fsSL -o /usr/local/bin/dbmate \
    https://github.com/amacneil/dbmate/releases/latest/download/dbmate-linux-amd64 && \
    chmod +x /usr/local/bin/dbmate && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=backend /app/api /app/api
COPY --from=frontend /app/web/dist /app/web/dist
COPY api/migrations /app/migrations
COPY docker-entrypoint.sh /app/

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
```

### docker-compose.yml (Monolith — same pattern as Illuminate)

```yaml
services:
  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: convox
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-convox}
      POSTGRES_DB: convox
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: pg_isready -U convox
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    healthcheck:
      test: redis-cli ping
      interval: 5s
      timeout: 5s
      retries: 5

  app:
    build: .
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    environment:
      DATABASE_URL: postgres://convox:${POSTGRES_PASSWORD:-convox}@postgres:5432/convox?sslmode=disable
      CONVOX_DATABASE_URL: postgres://convox:${POSTGRES_PASSWORD:-convox}@postgres:5432/convox?sslmode=disable
      CONVOX_REDIS_URL: redis://redis:6379
      CONVOX_JWT_SECRET: ${JWT_SECRET}
      CONVOX_ENCRYPT_KEY: ${ENCRYPT_KEY}
      CONVOX_ENV: production
      CONVOX_PORT: "8000"
    ports:
      - "8000:8000"
    restart: unless-stopped

volumes:
  pgdata:
```

---

## Performance Targets

| Metric | Target | Notes |
|--------|--------|-------|
| STT -> LLM -> TTS latency (cascade) | < 1.5s | End-to-end response time |
| PersonaPlex latency (Phase 2) | < 150ms | Full-duplex response |
| API response time (p99) | < 200ms | Control plane operations |
| Concurrent calls per node | 50-100 | Scale horizontally beyond |
| Transcript storage write | < 50ms | Async write |
| Dashboard page load | < 1.5s | Vite SPA with code splitting |
| Cost event accuracy | +/-2% | vs actual provider invoices |

---

## Observability Stack

### What to Instrument

1. **Structured logs** (structlog): every call event, provider call, cost event, compliance event
2. **Metrics** (Prometheus): calls_active, calls_total, call_duration_seconds, cost_usd_total, provider_latency_ms, error_rate
3. **Traces** (optional, Phase 2): OpenTelemetry for tracing STT/LLM/TTS calls
4. **Error tracking**: Sentry for exceptions + API errors
5. **Dashboard health**: Uptime monitoring (self-hosted Uptime Kuma or Healthchecks.io)

### Log Schema (structured)

```json
{
  "timestamp": "2026-02-24T10:00:00Z",
  "level": "info",
  "event": "call_turn_complete",
  "session_id": "uuid",
  "agent_id": "uuid",
  "team_id": "uuid",
  "turn_index": 3,
  "stt_provider": "sarvam",
  "stt_latency_ms": 320,
  "llm_provider": "openai",
  "llm_latency_ms": 780,
  "tts_provider": "gnani",
  "tts_latency_ms": 210,
  "total_latency_ms": 1310,
  "cost_usd": 0.0042
}
```

---

## Security Checklist (Pre-launch)

- [ ] All API endpoints require authentication (no public endpoints except `/health` and inbound telephony webhooks)
- [ ] Inbound telephony webhooks validated via signature (Twilio signature, Exotel HMAC)
- [ ] Phone numbers never stored in plaintext in audit/compliance tables
- [ ] Audio files encrypted at rest in MinIO (server-side encryption)
- [ ] Postgres password rotatable without downtime
- [ ] Rate limiting on all API endpoints
- [ ] SQL injection: all queries use parameterized `$1, $2` placeholders (asyncpg enforces this)
- [ ] XSS: React's default JSX escaping + CSP headers via Caddy
- [ ] CORS: explicit origin whitelist only
- [ ] Secrets: all via environment variables; no secrets in source code or Dockerfile
- [ ] DB migrations: tested rollback path for each migration
