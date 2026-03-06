from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from convox.database.postgres import get_pool
from convox.middleware.auth import get_current_user_id
from convox.model import Agent, AgentCreate, AgentList, AgentUpdate
from convox.repository import AgentRepo

router = APIRouter(prefix="/v1/agents", tags=["agents"])


def _repo() -> AgentRepo:
    return AgentRepo(get_pool())


@router.get("", response_model=AgentList)
async def list_agents(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> AgentList:
    agents, total = await _repo().list_by_user(user_id, limit, offset)
    return AgentList(agents=agents, total=total, limit=limit, offset=offset)


@router.post("", response_model=Agent, status_code=201)
async def create_agent(
    body: AgentCreate,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> Agent:
    return await _repo().create(body, user_id)


@router.get("/{agent_id}", response_model=Agent)
async def get_agent(
    agent_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> Agent:
    agent = await _repo().get_by_id(agent_id, user_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.put("/{agent_id}", response_model=Agent)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> Agent:
    agent = await _repo().update(agent_id, user_id, body)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> None:
    deleted = await _repo().delete(agent_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent not found")
