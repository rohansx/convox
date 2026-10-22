"""Engine and session factory. Only `store/` imports SQLAlchemy or a DB driver."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from convox.store.models import Base


def make_engine(url: str) -> AsyncEngine:
    kwargs: dict = {}
    if not url.startswith("sqlite"):
        kwargs["pool_pre_ping"] = True
    return create_async_engine(url, **kwargs)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """Dev/test schema setup: create_all, no migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ping(engine: AsyncEngine) -> None:
    """Raises if the database is unreachable."""
    async with engine.connect() as conn:
        await conn.exec_driver_sql("SELECT 1")
