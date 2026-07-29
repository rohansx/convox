# Convox — Project Structure (Illuminate-Style Monolith)

> Last updated: February 2026
> Status: Pre-implementation planning
> Reference: Modeled after `/home/rsx/Desktop/projx/illuminate`

---

## Overview

Convox follows the same **monolith architecture** as Illuminate:
- Single container deployment (frontend + backend)
- Makefile-driven development workflow
- Docker Compose with Postgres + Redis
- dbmate for SQL migrations (plain SQL, no ORM)
- asyncpg for database access (repository pattern, raw SQL)
- Frontend compiled to static files, served by the backend

The key differences from Illuminate:
- **Python (FastAPI)** replaces Go — Pipecat (voice AI framework) is Python-native
- **Vite + React** replaces SvelteKit — React ecosystem wins for dashboard (shadcn/ui, React Flow for visual builder)

---

## Directory Structure

```
convox/
├── docs/                              # Planning docs (this folder)
│   ├── architecture.md
│   ├── tech-specs.md
│   ├── phases.md
│   ├── project-plan.md
│   ├── python-vs-go.md
│   └── project-structure.md           # <- this file
│
├── api/                               # Python backend (FastAPI + Pipecat)
│   ├── cmd/                           # CLI entrypoints (like Illuminate's cmd/)
│   │   ├── server.py                  # Main server entrypoint
│   │   └── seed.py                    # Seed demo agents (optional)
│   │
│   ├── convox/                        # Main Python package
│   │   ├── __init__.py
│   │   ├── config.py                  # Pydantic Settings (env vars)
│   │   ├── app.py                     # FastAPI app factory + route registration
│   │   ├── database/                  # asyncpg pool + Redis client
│   │   ├── crypto/                    # JWT + AES encryption
│   │   ├── middleware/                # auth, cors, rate_limit, team_scope, logger
│   │   ├── model/                     # Pydantic domain models
│   │   ├── repository/               # Data access (raw SQL via asyncpg)
│   │   ├── service/                   # Business logic
│   │   ├── handler/                   # HTTP route handlers
│   │   ├── ws/                        # WebSocket handlers
│   │   ├── providers/                 # Voice AI provider plugins
│   │   │   ├── base.py               # Abstract base classes
│   │   │   ├── registry.py           # Provider lookup
│   │   │   ├── stt/                   # Sarvam, Deepgram, OpenAI, Riva
│   │   │   ├── llm/                   # OpenAI, Anthropic, Sarvam Saarika
│   │   │   ├── tts/                   # Gnani, ElevenLabs, OpenAI, Riva
│   │   │   └── telephony/             # Exotel, Twilio
│   │   └── compliance/               # Pluggable modules
│   │       ├── base.py
│   │       └── dpdp/                  # DPDP consent, retention, deletion, audit
│   │
│   ├── migrations/                    # dbmate SQL migrations
│   ├── db/                            # Schema dump
│   ├── tests/                         # pytest
│   └── pyproject.toml                 # Python deps (uv)
│
├── web/                               # Vite + React frontend
│   ├── src/
│   │   ├── main.tsx                   # App entrypoint
│   │   ├── App.tsx                    # Router setup (react-router-dom)
│   │   ├── index.css                  # Tailwind + global styles
│   │   ├── routes/                    # Page components
│   │   │   ├── Landing.tsx            # /
│   │   │   ├── Login.tsx              # /login
│   │   │   ├── Overview.tsx           # /app
│   │   │   ├── AgentList.tsx          # /app/agents
│   │   │   ├── AgentDetail.tsx        # /app/agents/:id
│   │   │   ├── AgentNew.tsx           # /app/agents/new
│   │   │   ├── SessionList.tsx        # /app/sessions
│   │   │   ├── SessionDetail.tsx      # /app/sessions/:id
│   │   │   ├── Analytics.tsx          # /app/analytics
│   │   │   ├── Providers.tsx          # /app/providers
│   │   │   ├── Compliance.tsx         # /app/compliance
│   │   │   └── Settings.tsx           # /app/settings
│   │   ├── components/
│   │   │   ├── ui/                    # shadcn/ui base components
│   │   │   ├── layout/               # DashboardLayout, Sidebar, Nav
│   │   │   ├── agents/               # AgentCard, AgentForm
│   │   │   ├── sessions/             # SessionRow, TranscriptView, CallPlayer
│   │   │   ├── analytics/            # CostChart, LatencyChart
│   │   │   └── compliance/           # ConsentLog
│   │   ├── lib/
│   │   │   ├── api.ts                # Typed API client
│   │   │   ├── auth.ts               # Auth state
│   │   │   ├── realtime.ts           # WebSocket client
│   │   │   └── utils.ts              # Helpers
│   │   └── types/                     # TypeScript types
│   │
│   ├── index.html                     # Vite HTML entrypoint
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── components.json                # shadcn/ui config
│   ├── package.json                   # Bun deps
│   └── bun.lock
│
├── Makefile                           # Development commands (see below)
├── Dockerfile                         # Multi-stage: frontend -> backend -> runtime
├── docker-compose.yml                 # postgres + redis + app
├── docker-compose.dev.yml             # Dev overrides (hot reload)
├── docker-entrypoint.sh               # Runs migrations, then starts server
├── Caddyfile                          # Reverse proxy for production
├── .env.example                       # All env vars documented
├── .env                               # Local dev (gitignored)
├── .gitignore
├── LICENSE                            # Apache 2.0
└── README.md                          # Getting started
```

---

## Makefile (Modeled After Illuminate)

```makefile
.PHONY: help up down logs install build clean \
        db-up db-down db-reset migrate-up migrate-down migrate-status migrate-new \
        api-dev api-build api-test web-dev web-build web-check fmt lint

# --- Quick start -------------------------------------------------------
help:                          ## Show all commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

up: db-up api-dev web-dev      ## Start everything (DB + API + frontend)

down:                          ## Stop all servers
	@kill $$(cat .pid.api 2>/dev/null) 2>/dev/null || true
	@kill $$(cat .pid.web 2>/dev/null) 2>/dev/null || true
	@rm -f .pid.api .pid.web
	@echo "Servers stopped"

logs:                          ## Tail logs from both servers
	@tail -f /tmp/convox-api.log /tmp/convox-web.log

# --- Dependencies -------------------------------------------------------
install:                       ## Install all dependencies
	cd api && uv sync
	cd web && bun install

# --- Database ------------------------------------------------------------
DB_CONTAINER := convox-postgres
DB_URL := postgres://convox:convox@localhost:5432/convox?sslmode=disable

db-up:                         ## Start PostgreSQL container
	@docker inspect $(DB_CONTAINER) > /dev/null 2>&1 || \
		docker run -d --name $(DB_CONTAINER) \
			-e POSTGRES_USER=convox \
			-e POSTGRES_PASSWORD=convox \
			-e POSTGRES_DB=convox \
			-p 5432:5432 \
			-v convox-pgdata:/var/lib/postgresql/data \
			postgres:17-alpine
	@docker start $(DB_CONTAINER) 2>/dev/null || true
	@echo "PostgreSQL running on :5432"
	@sleep 1
	@$(MAKE) migrate-up

db-down:                       ## Stop PostgreSQL
	@docker stop $(DB_CONTAINER) 2>/dev/null || true
	@echo "PostgreSQL stopped"

db-reset:                      ## Destroy and recreate DB (WARNING: deletes data)
	@docker rm -f $(DB_CONTAINER) 2>/dev/null || true
	@docker volume rm convox-pgdata 2>/dev/null || true
	@echo "Database destroyed"
	@$(MAKE) db-up

migrate-up:                    ## Run all pending migrations
	dbmate --url "$(DB_URL)" --migrations-dir api/migrations --no-dump-schema up

migrate-down:                  ## Rollback last migration
	dbmate --url "$(DB_URL)" --migrations-dir api/migrations --no-dump-schema down

migrate-status:                ## Show migration status
	dbmate --url "$(DB_URL)" --migrations-dir api/migrations status

migrate-new:                   ## Create new migration (usage: make migrate-new name=add_column)
	dbmate --url "$(DB_URL)" --migrations-dir api/migrations new $(name)

# --- Backend (Python/FastAPI) -------------------------------------------
api-dev:                       ## Start API server with hot reload
	cd api && uv run uvicorn convox.app:create_app --factory \
		--host 0.0.0.0 --port 8000 --reload \
		> /tmp/convox-api.log 2>&1 & echo $$! > ../.pid.api
	@echo "API server running on :8000 (PID: $$(cat .pid.api))"

api-build:                     ## Type check backend
	cd api && uv run mypy convox/

api-test:                      ## Run tests
	cd api && uv run pytest tests/ -v

# --- Frontend (Vite + React) --------------------------------------------
web-dev:                       ## Start frontend dev server
	cd web && bun run dev --port 5173 \
		> /tmp/convox-web.log 2>&1 & echo $$! > ../.pid.web
	@echo "Frontend running on :5173 (PID: $$(cat .pid.web))"

web-build:                     ## Build frontend for production
	cd web && bun run build

web-check:                     ## Type check frontend
	cd web && bun run tsc --noEmit

# --- Full build ----------------------------------------------------------
build: web-build api-build     ## Full production build

# --- Code quality --------------------------------------------------------
fmt:                           ## Format code
	cd api && uv run ruff format convox/ tests/

lint:                          ## Lint code
	cd api && uv run ruff check convox/ tests/
	cd api && uv run mypy convox/
	cd web && bun run tsc --noEmit

clean:                         ## Remove build artifacts
	rm -rf web/dist api/__pycache__
	find api -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
```

---

## Docker Configuration

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

# Stage 2: Python backend
FROM python:3.12-slim AS backend
WORKDIR /app
RUN pip install uv
COPY api/pyproject.toml api/uv.lock ./api/
RUN cd api && uv sync --frozen --no-dev
COPY api/ ./api/

# Stage 3: Runtime (single container — frontend + backend)
FROM python:3.12-slim
WORKDIR /app

# Install dbmate for migrations
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

### docker-compose.yml

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

### docker-entrypoint.sh

```bash
#!/bin/sh
set -e
echo "Running migrations..."
dbmate --url "$DATABASE_URL" --migrations-dir ./migrations --no-dump-schema up
echo "Starting Convox..."
cd api && uv run uvicorn convox.app:create_app --factory --host 0.0.0.0 --port 8000
```

---

## How It Works Together (Monolith Runtime)

Same pattern as Illuminate — single process serves everything:

```
User Browser
    |
    v
Caddy (reverse proxy, auto-TLS)
    |
    v
FastAPI Server (uvicorn, port 8000)
    |-- Static file request?  --> Serve from web/dist/
    |-- /health               --> Health check handler
    |-- /auth/*               --> Auth handlers (API keys, JWT)
    |-- /v1/*                 --> API handlers -> Service -> Repository -> Postgres
    |-- /ws/*                 --> WebSocket -> Pipecat pipeline (in-process)
    |-- /inbound/*            --> Telephony webhook handler
    '-- Not found?            --> SPA fallback (index.html from web/dist/)
```

In production, the FastAPI server:
1. Serves the Vite React build at `/` (static files + SPA fallback)
2. Handles all API routes at `/v1/*`
3. Manages WebSocket connections for live test calls
4. Spawns Pipecat pipelines in-process for voice calls
5. Runs background scheduled tasks (retention, analytics)

---

## Key Differences from Illuminate

| Aspect | Illuminate | Convox |
|--------|-----------|--------|
| Backend language | Go 1.25 | Python 3.12 (FastAPI) |
| HTTP router | chi/v5 | FastAPI (Starlette) |
| DB driver | pgx (Go) | asyncpg (Python) |
| DB access pattern | Repository + raw SQL | Repository + raw SQL (identical) |
| Package manager | go mod | uv |
| Test framework | go test | pytest |
| Linter | golangci-lint | ruff + mypy |
| Config loading | envconfig | pydantic-settings |
| Background tasks | goroutine scheduler | asyncio tasks |
| Frontend framework | SvelteKit | Vite + React |
| Frontend build output | `web/build/` | `web/dist/` |
| Static file serving | Go http.FileServer | FastAPI StaticFiles |
| Postgres | Identical | Identical |
| Redis | Identical | Identical |
| Migrations | dbmate (identical) | dbmate (identical) |
| Docker pattern | Identical | Identical |
| Makefile pattern | Identical | Identical |
| Reverse proxy | Caddy (identical) | Caddy (identical) |

**The infrastructure is identical.** Backend language and frontend framework differ — everything else is the same.

---

## Migration Naming Convention

Following Illuminate's pattern:

```
YYYYMMDDHHMMSS_description.sql

Examples:
20260225000001_create_users.sql
20260225000002_create_agents.sql
20260225000003_create_sessions.sql
20260225000004_create_transcripts.sql
20260225000005_create_cost_events.sql
20260225000006_create_consent_events.sql
20260225000007_create_audit_log.sql
20260225000008_create_provider_configs.sql
```

Each migration file:

```sql
-- migrate:up
CREATE TABLE agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ...
);

-- migrate:down
DROP TABLE IF EXISTS agents;
```
