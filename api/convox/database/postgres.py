import asyncpg
import structlog

logger = structlog.get_logger()

_pool: asyncpg.Pool | None = None


async def create_pool(database_url: str) -> asyncpg.Pool:
    global _pool
    # asyncpg expects postgres:// not postgresql://
    url = database_url.replace("postgresql://", "postgres://")
    _pool = await asyncpg.create_pool(
        url,
        min_size=2,
        max_size=10,
        max_inactive_connection_lifetime=300,
    )
    logger.info("postgres_pool_created", min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("postgres_pool_closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call create_pool() first.")
    return _pool
