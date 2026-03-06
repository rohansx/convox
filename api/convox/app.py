from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from convox.config import settings
from convox.database.postgres import close_pool, create_pool
from convox.database.redis import close_redis, create_redis
from convox.handler import agents, analytics, auth, health, providers, sessions
from convox.middleware.cors import setup_cors
from convox.middleware.logger import LoggerMiddleware

logger = structlog.get_logger()

# Path to frontend build output
WEB_DIST = Path(__file__).resolve().parent.parent.parent / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    logger.info("convox_starting", env=settings.env, port=settings.port)
    await create_pool(settings.database_url)
    await create_redis(settings.redis_url)
    yield
    # Shutdown
    await close_pool()
    await close_redis()
    logger.info("convox_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Convox",
        description="Open-source voice AI orchestration platform",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url=None,
    )

    # Middleware
    setup_cors(app)
    app.add_middleware(LoggerMiddleware)

    # API routes
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(agents.router)
    app.include_router(sessions.router)
    app.include_router(analytics.router)
    app.include_router(providers.router)

    # Serve frontend static files in production
    if WEB_DIST.exists() and not settings.is_development:
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str) -> FileResponse:
            # Try exact file first, then fall back to index.html for SPA routing
            file_path = WEB_DIST / full_path
            if file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(WEB_DIST / "index.html")

    return app
