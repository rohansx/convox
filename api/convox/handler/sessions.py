from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from convox.database.postgres import get_pool
from convox.middleware.auth import get_current_user_id
from convox.model import Session, SessionCreate, SessionList, TranscriptList
from convox.repository import CostEventRepo, SessionRepo, TranscriptRepo

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def _session_repo() -> SessionRepo:
    return SessionRepo(get_pool())


@router.get("", response_model=SessionList)
async def list_sessions(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    agent_id: uuid.UUID | None = None,
    status: str | None = None,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> SessionList:
    sessions, total = await _session_repo().list_by_user(
        user_id, agent_id=agent_id, status=status, limit=limit, offset=offset,
    )
    return SessionList(sessions=sessions, total=total, limit=limit, offset=offset)


@router.post("", response_model=Session, status_code=201)
async def create_session(
    body: SessionCreate,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> Session:
    return await _session_repo().create(body, user_id)


@router.get("/{session_id}", response_model=Session)
async def get_session(
    session_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> Session:
    session = await _session_repo().get_by_id(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/{session_id}/transcript", response_model=TranscriptList)
async def get_transcript(
    session_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> TranscriptList:
    session = await _session_repo().get_by_id(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    transcripts = await TranscriptRepo(get_pool()).list_by_session(session_id)
    return TranscriptList(transcripts=transcripts, session_id=session_id)


@router.get("/{session_id}/cost")
async def get_session_cost(
    session_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> dict:
    session = await _session_repo().get_by_id(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    events = await CostEventRepo(get_pool()).list_by_session(session_id)
    total = sum(float(e.amount_usd) for e in events)
    return {
        "session_id": str(session_id),
        "events": [e.model_dump(mode="json") for e in events],
        "total_usd": total,
    }
