from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request

from convox.crypto import decode_jwt
from convox.database.postgres import get_pool
from convox.repository import UserRepo


async def get_current_user_id(request: Request) -> uuid.UUID:
    """Extract user from API key header or JWT cookie. Returns user UUID."""
    pool = get_pool()
    repo = UserRepo(pool)

    # 1. Check X-API-Key header
    api_key = request.headers.get("x-api-key")
    if api_key:
        user = await repo.get_by_api_key(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return user.id

    # 2. Check Authorization: Bearer <jwt>
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        payload = decode_jwt(token)
        if not payload or "sub" not in payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = uuid.UUID(payload["sub"])
        user = await repo.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user.id

    raise HTTPException(status_code=401, detail="Authentication required. Provide X-API-Key header or Bearer token.")


# FastAPI dependency
CurrentUser = Depends(get_current_user_id)
