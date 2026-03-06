from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from convox.config import settings


def setup_cors(app: FastAPI) -> None:
    origins = [settings.frontend_url]
    if settings.is_development:
        origins.append("http://localhost:5173")
        origins.append("http://localhost:8000")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
