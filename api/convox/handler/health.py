from fastapi import APIRouter

from convox.database.postgres import get_pool
from convox.database.redis import get_redis

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    status: dict = {"status": "ok"}

    # Check postgres
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        status["postgres"] = "ok"
    except Exception:
        status["postgres"] = "error"
        status["status"] = "degraded"

    # Check redis
    r = get_redis()
    if r:
        try:
            await r.ping()
            status["redis"] = "ok"
        except Exception:
            status["redis"] = "error"
    else:
        status["redis"] = "unavailable"

    return status
