import redis.asyncio as aioredis
import structlog

logger = structlog.get_logger()

_redis: aioredis.Redis | None = None


async def create_redis(redis_url: str) -> aioredis.Redis | None:
    global _redis
    try:
        _redis = aioredis.from_url(redis_url, decode_responses=True)
        await _redis.ping()
        logger.info("redis_connected", url=redis_url)
        return _redis
    except Exception as e:
        logger.warning("redis_unavailable", error=str(e))
        _redis = None
        return None


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None
        logger.info("redis_closed")


def get_redis() -> aioredis.Redis | None:
    return _redis
