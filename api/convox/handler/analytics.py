from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from convox.database.postgres import get_pool
from convox.middleware.auth import get_current_user_id
from convox.repository import CostEventRepo, SessionRepo

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


@router.get("/overview")
async def overview(user_id: uuid.UUID = Depends(get_current_user_id)) -> dict:
    pool = get_pool()
    session_repo = SessionRepo(pool)
    cost_repo = CostEventRepo(pool)

    active_calls = await session_repo.count_active(user_id)
    total_cost = await cost_repo.total_cost_by_user(user_id)

    total_sessions = await pool.fetchval(
        "SELECT count(*) FROM sessions WHERE user_id = $1", user_id,
    )
    today_sessions = await pool.fetchval(
        "SELECT count(*) FROM sessions WHERE user_id = $1 AND created_at >= CURRENT_DATE",
        user_id,
    )

    return {
        "active_calls": active_calls,
        "total_sessions": total_sessions,
        "today_sessions": today_sessions,
        "total_cost_usd": float(total_cost),
    }
