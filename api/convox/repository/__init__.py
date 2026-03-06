from __future__ import annotations

import json
import uuid

import asyncpg

from convox.model import (
    Agent,
    AgentCreate,
    AgentUpdate,
    CostEvent,
    ProviderConfig,
    ProviderConfigCreate,
    Session,
    SessionCreate,
    Transcript,
    User,
    UserCreate,
)


def _row_to_dict(row: asyncpg.Record) -> dict:
    d = dict(row)
    # asyncpg returns jsonb as str, parse it
    for k, v in d.items():
        if isinstance(v, str) and k in ("config", "credentials"):
            try:
                d[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# ── User Repository ────────────────────────────────────────────────

class UserRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        row = await self.pool.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        return User(**_row_to_dict(row)) if row else None

    async def get_by_email(self, email: str) -> User | None:
        row = await self.pool.fetchrow("SELECT * FROM users WHERE email = $1", email)
        return User(**_row_to_dict(row)) if row else None

    async def get_by_api_key(self, api_key: str) -> User | None:
        row = await self.pool.fetchrow("SELECT * FROM users WHERE api_key = $1", api_key)
        return User(**_row_to_dict(row)) if row else None

    async def create(self, data: UserCreate, api_key: str) -> User:
        row = await self.pool.fetchrow(
            """INSERT INTO users (email, name, role, api_key)
               VALUES ($1, $2, $3, $4) RETURNING *""",
            data.email, data.name, data.role, api_key,
        )
        return User(**_row_to_dict(row))

    async def list_all(self) -> list[User]:
        rows = await self.pool.fetch("SELECT * FROM users ORDER BY created_at DESC")
        return [User(**_row_to_dict(r)) for r in rows]


# ── Agent Repository ───────────────────────────────────────────────

class AgentRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get_by_id(self, agent_id: uuid.UUID, user_id: uuid.UUID) -> Agent | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM agents WHERE id = $1 AND user_id = $2", agent_id, user_id,
        )
        return Agent(**_row_to_dict(row)) if row else None

    async def list_by_user(self, user_id: uuid.UUID, limit: int = 50, offset: int = 0) -> tuple[list[Agent], int]:
        total = await self.pool.fetchval(
            "SELECT count(*) FROM agents WHERE user_id = $1", user_id,
        )
        rows = await self.pool.fetch(
            """SELECT * FROM agents WHERE user_id = $1
               ORDER BY created_at DESC LIMIT $2 OFFSET $3""",
            user_id, limit, offset,
        )
        return [Agent(**_row_to_dict(r)) for r in rows], total

    async def create(self, data: AgentCreate, user_id: uuid.UUID) -> Agent:
        row = await self.pool.fetchrow(
            """INSERT INTO agents (name, description, config, user_id)
               VALUES ($1, $2, $3::jsonb, $4) RETURNING *""",
            data.name, data.description, json.dumps(data.config), user_id,
        )
        return Agent(**_row_to_dict(row))

    async def update(self, agent_id: uuid.UUID, user_id: uuid.UUID, data: AgentUpdate) -> Agent | None:
        existing = await self.get_by_id(agent_id, user_id)
        if not existing:
            return None
        name = data.name if data.name is not None else existing.name
        desc = data.description if data.description is not None else existing.description
        config = data.config if data.config is not None else existing.config
        row = await self.pool.fetchrow(
            """UPDATE agents SET name = $1, description = $2, config = $3::jsonb, updated_at = now()
               WHERE id = $4 AND user_id = $5 RETURNING *""",
            name, desc, json.dumps(config), agent_id, user_id,
        )
        return Agent(**_row_to_dict(row)) if row else None

    async def delete(self, agent_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self.pool.execute(
            "DELETE FROM agents WHERE id = $1 AND user_id = $2", agent_id, user_id,
        )
        return result == "DELETE 1"


# ── Session Repository ─────────────────────────────────────────────

class SessionRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get_by_id(self, session_id: uuid.UUID, user_id: uuid.UUID) -> Session | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM sessions WHERE id = $1 AND user_id = $2", session_id, user_id,
        )
        return Session(**_row_to_dict(row)) if row else None

    async def list_by_user(
        self, user_id: uuid.UUID, agent_id: uuid.UUID | None = None,
        status: str | None = None, limit: int = 50, offset: int = 0,
    ) -> tuple[list[Session], int]:
        conditions = ["user_id = $1"]
        params: list = [user_id]
        idx = 2

        if agent_id:
            conditions.append(f"agent_id = ${idx}")
            params.append(agent_id)
            idx += 1
        if status:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1

        where = " AND ".join(conditions)
        total = await self.pool.fetchval(f"SELECT count(*) FROM sessions WHERE {where}", *params)
        rows = await self.pool.fetch(
            f"""SELECT * FROM sessions WHERE {where}
                ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}""",
            *params, limit, offset,
        )
        return [Session(**_row_to_dict(r)) for r in rows], total

    async def create(self, data: SessionCreate, user_id: uuid.UUID) -> Session:
        row = await self.pool.fetchrow(
            """INSERT INTO sessions (agent_id, direction, caller_number, telephony_provider, user_id)
               VALUES ($1, $2, $3, $4, $5) RETURNING *""",
            data.agent_id, data.direction, data.caller_number, data.telephony_provider, user_id,
        )
        return Session(**_row_to_dict(row))

    async def update_status(self, session_id: uuid.UUID, status: str) -> Session | None:
        extra = ""
        if status == "active":
            extra = ", started_at = now()"
        elif status in ("completed", "failed"):
            extra = ", ended_at = now()"
        row = await self.pool.fetchrow(
            f"UPDATE sessions SET status = $1{extra} WHERE id = $2 RETURNING *",
            status, session_id,
        )
        return Session(**_row_to_dict(row)) if row else None

    async def update_cost(self, session_id: uuid.UUID, cost: float) -> None:
        await self.pool.execute(
            "UPDATE sessions SET cost_usd_total = $1 WHERE id = $2",
            cost, session_id,
        )

    async def count_active(self, user_id: uuid.UUID) -> int:
        return await self.pool.fetchval(
            "SELECT count(*) FROM sessions WHERE user_id = $1 AND status = 'active'", user_id,
        )


# ── Transcript Repository ──────────────────────────────────────────

class TranscriptRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def list_by_session(self, session_id: uuid.UUID) -> list[Transcript]:
        rows = await self.pool.fetch(
            "SELECT * FROM transcripts WHERE session_id = $1 ORDER BY turn_index",
            session_id,
        )
        return [Transcript(**_row_to_dict(r)) for r in rows]

    async def create(
        self, session_id: uuid.UUID, turn_index: int, speaker: str, text: str,
        timestamp_ms: int, stt_provider: str | None = None,
        stt_confidence: float | None = None, stt_cost_usd: float = 0,
    ) -> Transcript:
        row = await self.pool.fetchrow(
            """INSERT INTO transcripts
               (session_id, turn_index, speaker, text, timestamp_ms, stt_provider, stt_confidence, stt_cost_usd)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *""",
            session_id, turn_index, speaker, text, timestamp_ms,
            stt_provider, stt_confidence, stt_cost_usd,
        )
        return Transcript(**_row_to_dict(row))


# ── Cost Event Repository ──────────────────────────────────────────

class CostEventRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def list_by_session(self, session_id: uuid.UUID) -> list[CostEvent]:
        rows = await self.pool.fetch(
            "SELECT * FROM cost_events WHERE session_id = $1 ORDER BY created_at",
            session_id,
        )
        return [CostEvent(**_row_to_dict(r)) for r in rows]

    async def create(
        self, session_id: uuid.UUID, event_type: str, provider: str,
        amount_usd: float, units: float, unit_type: str = "seconds",
    ) -> CostEvent:
        row = await self.pool.fetchrow(
            """INSERT INTO cost_events (session_id, event_type, provider, amount_usd, units, unit_type)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING *""",
            session_id, event_type, provider, amount_usd, units, unit_type,
        )
        return CostEvent(**_row_to_dict(row))

    async def total_cost_by_user(self, user_id: uuid.UUID) -> float:
        result = await self.pool.fetchval(
            """SELECT COALESCE(SUM(ce.amount_usd), 0)
               FROM cost_events ce JOIN sessions s ON s.id = ce.session_id
               WHERE s.user_id = $1""",
            user_id,
        )
        return float(result)


# ── Provider Config Repository ─────────────────────────────────────

class ProviderConfigRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def list_by_user(self, user_id: uuid.UUID) -> list[ProviderConfig]:
        rows = await self.pool.fetch(
            "SELECT * FROM provider_configs WHERE user_id = $1 ORDER BY category, provider",
            user_id,
        )
        return [ProviderConfig(**_row_to_dict(r)) for r in rows]

    async def upsert(self, data: ProviderConfigCreate, user_id: uuid.UUID) -> ProviderConfig:
        row = await self.pool.fetchrow(
            """INSERT INTO provider_configs (user_id, provider, category, credentials, is_active)
               VALUES ($1, $2, $3, $4::jsonb, $5)
               ON CONFLICT (user_id, provider, category)
               DO UPDATE SET credentials = $4::jsonb, is_active = $5, updated_at = now()
               RETURNING *""",
            user_id, data.provider, data.category,
            json.dumps(data.credentials), data.is_active,
        )
        return ProviderConfig(**_row_to_dict(row))
