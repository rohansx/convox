"""Platform API keys: `Authorization: Bearer <key>`, keys from settings.platform_api_keys
("id:key,id2:key2"). Keys are compared as SHA-256 digests with `secrets.compare_digest`
against every configured key, so neither timing nor early exit leaks which key matched.

env=test: a request with no Authorization header is allowed as platform "test" (tests
and local scripts need no key); a wrong key is still a 401. env=prod: keys left at a
default value ("change-me", empty) are ignored.
"""

from __future__ import annotations

import hashlib
import secrets

from starlette.requests import HTTPConnection

from convox.api.schemas import ApiError
from convox.config.settings import Settings

DEFAULT_SECRETS = frozenset({"", "change-me"})
TEST_PLATFORM = "test"


def parse_api_keys(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in raw.split(","):
        pid, sep, key = part.strip().partition(":")
        if sep and pid and key:
            out[pid] = key
    return out


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def match_platform(token: str, settings: Settings) -> str | None:
    keys = parse_api_keys(settings.platform_api_keys)
    if settings.convox_env == "prod":
        keys = {pid: k for pid, k in keys.items() if k not in DEFAULT_SECRETS}
    presented = _digest(token)
    found: str | None = None
    for pid, key in keys.items():
        if secrets.compare_digest(presented, _digest(key)):
            found = pid
    return found


def require_platform_key(conn: HTTPConnection) -> str:
    """FastAPI dependency: the platform id for the presented bearer key."""
    settings: Settings = conn.app.state.settings
    header = conn.headers.get("authorization")
    if header is None and settings.convox_env == "test":
        return TEST_PLATFORM
    scheme, _, token = (header or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "unauthorized", "missing bearer api key")
    pid = match_platform(token.strip(), settings)
    if pid is None:
        raise ApiError(401, "unauthorized", "invalid api key")
    return pid
