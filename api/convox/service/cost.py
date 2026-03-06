from __future__ import annotations

import uuid
from decimal import Decimal

import structlog

from convox.database.postgres import get_pool
from convox.repository import CostEventRepo, SessionRepo

logger = structlog.get_logger()


class CostTracker:
    """Records cost events and keeps session totals in sync."""

    def __init__(self) -> None:
        pool = get_pool()
        self.cost_repo = CostEventRepo(pool)
        self.session_repo = SessionRepo(pool)

    async def record_stt(
        self,
        session_id: uuid.UUID,
        provider: str,
        audio_duration_sec: float,
        cost_per_second: float,
    ) -> Decimal:
        """Record an STT cost event. Returns the cost in USD."""
        amount = round(audio_duration_sec * cost_per_second, 8)
        await self.cost_repo.create(
            session_id=session_id,
            event_type="stt",
            provider=provider,
            amount_usd=amount,
            units=audio_duration_sec,
            unit_type="seconds",
        )
        await self._refresh_session_total(session_id)
        logger.info(
            "cost_recorded",
            type="stt",
            provider=provider,
            session_id=str(session_id),
            amount_usd=amount,
            units=audio_duration_sec,
        )
        return Decimal(str(amount))

    async def record_tts(
        self,
        session_id: uuid.UUID,
        provider: str,
        char_count: int,
        cost_per_char: float,
    ) -> Decimal:
        """Record a TTS cost event. Returns the cost in USD."""
        amount = round(char_count * cost_per_char, 8)
        await self.cost_repo.create(
            session_id=session_id,
            event_type="tts",
            provider=provider,
            amount_usd=amount,
            units=float(char_count),
            unit_type="characters",
        )
        await self._refresh_session_total(session_id)
        logger.info(
            "cost_recorded",
            type="tts",
            provider=provider,
            session_id=str(session_id),
            amount_usd=amount,
            units=char_count,
        )
        return Decimal(str(amount))

    async def record_llm(
        self,
        session_id: uuid.UUID,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost_per_1k_input: float,
        cost_per_1k_output: float,
    ) -> Decimal:
        """Record an LLM cost event. Returns the cost in USD."""
        amount = round(
            (input_tokens * cost_per_1k_input / 1000)
            + (output_tokens * cost_per_1k_output / 1000),
            8,
        )
        total_tokens = input_tokens + output_tokens
        await self.cost_repo.create(
            session_id=session_id,
            event_type="llm",
            provider=provider,
            amount_usd=amount,
            units=float(total_tokens),
            unit_type="tokens",
        )
        await self._refresh_session_total(session_id)
        logger.info(
            "cost_recorded",
            type="llm",
            provider=provider,
            session_id=str(session_id),
            amount_usd=amount,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return Decimal(str(amount))

    async def record_telephony(
        self,
        session_id: uuid.UUID,
        provider: str,
        duration_sec: float,
        cost_per_second: float,
    ) -> Decimal:
        """Record a telephony cost event. Returns the cost in USD."""
        amount = round(duration_sec * cost_per_second, 8)
        await self.cost_repo.create(
            session_id=session_id,
            event_type="telephony",
            provider=provider,
            amount_usd=amount,
            units=duration_sec,
            unit_type="seconds",
        )
        await self._refresh_session_total(session_id)
        logger.info(
            "cost_recorded",
            type="telephony",
            provider=provider,
            session_id=str(session_id),
            amount_usd=amount,
            units=duration_sec,
        )
        return Decimal(str(amount))

    async def _refresh_session_total(self, session_id: uuid.UUID) -> None:
        """Recompute and update the denormalized session cost total."""
        pool = get_pool()
        total = await pool.fetchval(
            "SELECT COALESCE(SUM(amount_usd), 0) FROM cost_events WHERE session_id = $1",
            session_id,
        )
        await self.session_repo.update_cost(session_id, float(total))

    async def get_breakdown_by_type(self, user_id: uuid.UUID) -> list[dict]:
        """Cost breakdown by event type for a user."""
        pool = get_pool()
        rows = await pool.fetch(
            """SELECT ce.event_type, SUM(ce.amount_usd) as total_usd, COUNT(*) as event_count
               FROM cost_events ce JOIN sessions s ON s.id = ce.session_id
               WHERE s.user_id = $1
               GROUP BY ce.event_type ORDER BY total_usd DESC""",
            user_id,
        )
        return [
            {
                "event_type": r["event_type"],
                "total_usd": float(r["total_usd"]),
                "event_count": r["event_count"],
            }
            for r in rows
        ]

    async def get_breakdown_by_provider(self, user_id: uuid.UUID) -> list[dict]:
        """Cost breakdown by provider for a user."""
        pool = get_pool()
        rows = await pool.fetch(
            """SELECT ce.provider, ce.event_type, SUM(ce.amount_usd) as total_usd,
                      SUM(ce.units) as total_units, ce.unit_type, COUNT(*) as event_count
               FROM cost_events ce JOIN sessions s ON s.id = ce.session_id
               WHERE s.user_id = $1
               GROUP BY ce.provider, ce.event_type, ce.unit_type
               ORDER BY total_usd DESC""",
            user_id,
        )
        return [
            {
                "provider": r["provider"],
                "event_type": r["event_type"],
                "total_usd": float(r["total_usd"]),
                "total_units": float(r["total_units"]),
                "unit_type": r["unit_type"],
                "event_count": r["event_count"],
            }
            for r in rows
        ]

    async def get_daily_costs(self, user_id: uuid.UUID, days: int = 30) -> list[dict]:
        """Daily cost totals for the last N days."""
        pool = get_pool()
        rows = await pool.fetch(
            """SELECT DATE(ce.created_at) as day, SUM(ce.amount_usd) as total_usd, COUNT(*) as events
               FROM cost_events ce JOIN sessions s ON s.id = ce.session_id
               WHERE s.user_id = $1 AND ce.created_at >= now() - ($2 || ' days')::interval
               GROUP BY DATE(ce.created_at) ORDER BY day""",
            user_id, str(days),
        )
        return [
            {"day": str(r["day"]), "total_usd": float(r["total_usd"]), "events": r["events"]}
            for r in rows
        ]
