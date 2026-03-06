from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from convox.crypto import create_jwt, generate_api_key
from convox.database.postgres import get_pool
from convox.middleware.auth import get_current_user_id
from convox.model import User, UserCreate
from convox.repository import UserRepo

router = APIRouter(tags=["auth"])


class BootstrapResponse(BaseModel):
    user: User
    api_key: str
    jwt_token: str


class TokenResponse(BaseModel):
    token: str
    user: User


@router.post("/auth/bootstrap", response_model=BootstrapResponse)
async def bootstrap(body: UserCreate) -> BootstrapResponse:
    """Create the first user. Only works when no users exist."""
    repo = UserRepo(get_pool())
    users = await repo.list_all()
    if users:
        raise HTTPException(status_code=409, detail="Users already exist. Use API key to authenticate.")
    api_key = generate_api_key()
    user = await repo.create(body, api_key)
    token = create_jwt(str(user.id))
    return BootstrapResponse(user=user, api_key=api_key, jwt_token=token)


@router.post("/auth/token", response_model=TokenResponse)
async def get_token(user_id: uuid.UUID = Depends(get_current_user_id)) -> TokenResponse:
    """Exchange API key for a short-lived JWT."""
    repo = UserRepo(get_pool())
    user = await repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    token = create_jwt(str(user.id))
    return TokenResponse(token=token, user=user)
