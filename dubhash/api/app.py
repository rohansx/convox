"""FastAPI app factory. Run with exactly one worker: both legs of a call must land in the
same process (docs/engineering/deployment.md).

    uvicorn dubhash.api.app:app --host 0.0.0.0 --port 8000 --workers 1
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from dubhash.api import calls, console, health, legs_ws, login, loopback, site, webhooks
from dubhash.api.deps import CallRegistry, InMemoryCallRegistry
from dubhash.api.schemas import ApiError, api_error_handler
from dubhash.config.settings import Settings, get_settings
from dubhash.events import EventBus
from dubhash.join import routes as join_routes
from dubhash.logs import setup_logging
from dubhash.store.db import init_db, make_engine, make_sessionmaker, ping
from dubhash.store.writer import TurnWriter
from dubhash.ward import routes as ward_routes

log = logging.getLogger(__name__)

LEGS_STATIC_DIR = Path(__file__).resolve().parent.parent / "legs" / "static"
DB_CONNECT_TIMEOUT_S = 3.0


async def _open_store(app: FastAPI, settings: Settings) -> Any:
    """Create tables and a writer if the DB answers; otherwise run without storage."""
    engine = make_engine(settings.database_url)
    try:
        await asyncio.wait_for(ping(engine), DB_CONNECT_TIMEOUT_S)
        await init_db(engine)
    except Exception as e:  # noqa: BLE001 - storage is optional for a live call
        log.warning("store.unavailable", extra={"error": repr(e)})
        await engine.dispose()
        return None
    app.state.engine = engine
    return TurnWriter(make_sessionmaker(engine))


async def _feed_registry(bus: EventBus, registry: InMemoryCallRegistry) -> None:
    async for ev in bus.subscribe():
        registry.apply_event(ev)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    setup_logging(settings)  # JSON lines to logs/dubhash.log (not in env=test)
    if app.state.writer is None and settings.dubhash_env != "test":
        app.state.writer = await _open_store(app, settings)
    if app.state.writer is not None:
        app.state.writer.start()
    feeder = None
    if isinstance(app.state.registry, InMemoryCallRegistry):
        feeder = asyncio.create_task(_feed_registry(app.state.bus, app.state.registry))
    try:
        yield
    finally:
        if feeder is not None:
            feeder.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await feeder
        recorders = getattr(app.state, "recorders", None)
        if recorders is not None:  # store/recorder.py: flush calls still being recorded
            await recorders.aclose()
        if app.state.writer is not None:
            await app.state.writer.close()
        engine = getattr(app.state, "engine", None)
        if engine is not None:
            await engine.dispose()


def create_app(
    settings: Settings | None = None,
    *,
    bus: EventBus | None = None,
    registry: CallRegistry | None = None,
    writer: TurnWriter | None = None,
) -> FastAPI:
    app = FastAPI(title="dubhash", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.state.bus = bus or EventBus()
    app.state.registry = registry if registry is not None else InMemoryCallRegistry()
    app.state.writer = writer
    app.state.engine = None

    app.include_router(health.router)
    app.include_router(login.router)
    app.include_router(console.router)
    app.include_router(calls.router)
    app.include_router(legs_ws.router)
    app.include_router(webhooks.router)
    app.include_router(loopback.http)
    app.include_router(loopback.router)
    app.include_router(join_routes.phone)
    app.include_router(join_routes.host)
    app.include_router(site.public)  # before site.demo: /demo/preview is not a call id
    app.include_router(site.demo)
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(ward_routes.router)
    app.mount("/static/console", StaticFiles(directory=console.STATIC_DIR), name="console-static")
    if LEGS_STATIC_DIR.is_dir():
        app.mount("/static/legs", StaticFiles(directory=LEGS_STATIC_DIR), name="legs-static")
    return app


app = create_app()
