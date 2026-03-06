from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

# ── Enums ──────────────────────────────────────────────────────────

class UserRole(StrEnum):
    owner = "owner"
    admin = "admin"
    developer = "developer"
    analyst = "analyst"
    readonly = "readonly"


class SessionDirection(StrEnum):
    inbound = "inbound"
    outbound = "outbound"


class SessionStatus(StrEnum):
    pending = "pending"
    active = "active"
    completed = "completed"
    failed = "failed"


class Speaker(StrEnum):
    user = "user"
    agent = "agent"


class CostEventType(StrEnum):
    stt = "stt"
    llm = "llm"
    tts = "tts"
    telephony = "telephony"


class UnitType(StrEnum):
    seconds = "seconds"
    tokens = "tokens"
    characters = "characters"


class ProviderCategory(StrEnum):
    stt = "stt"
    llm = "llm"
    tts = "tts"
    telephony = "telephony"


# ── User ───────────────────────────────────────────────────────────

class User(BaseModel):
    id: uuid.UUID
    email: str | None = None
    name: str
    role: UserRole
    api_key: str | None = None
    created_at: datetime
    updated_at: datetime


class UserCreate(BaseModel):
    email: str
    name: str
    role: UserRole = UserRole.admin


# ── Agent ──────────────────────────────────────────────────────────

class Agent(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    config: dict = Field(default_factory=dict)
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class AgentCreate(BaseModel):
    name: str
    description: str | None = None
    config: dict = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    config: dict | None = None


# ── Session ────────────────────────────────────────────────────────

class Session(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    direction: SessionDirection
    status: SessionStatus
    caller_number: str | None = None
    telephony_provider: str | None = None
    cost_usd_total: Decimal = Decimal("0")
    started_at: datetime | None = None
    ended_at: datetime | None = None
    user_id: uuid.UUID
    created_at: datetime


class SessionCreate(BaseModel):
    agent_id: uuid.UUID
    direction: SessionDirection = SessionDirection.outbound
    caller_number: str | None = None
    telephony_provider: str | None = None


# ── Transcript ─────────────────────────────────────────────────────

class Transcript(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    turn_index: int
    speaker: Speaker
    text: str
    timestamp_ms: int
    stt_provider: str | None = None
    stt_confidence: float | None = None
    stt_cost_usd: Decimal = Decimal("0")
    created_at: datetime


# ── Cost Event ─────────────────────────────────────────────────────

class CostEvent(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    event_type: CostEventType
    provider: str
    amount_usd: Decimal
    units: float
    unit_type: UnitType
    created_at: datetime


# ── Provider Config ────────────────────────────────────────────────

class ProviderConfig(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    provider: str
    category: ProviderCategory
    credentials: dict = Field(default_factory=dict)
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class ProviderConfigCreate(BaseModel):
    provider: str
    category: ProviderCategory
    credentials: dict = Field(default_factory=dict)
    is_active: bool = True


# ── Pagination ─────────────────────────────────────────────────────

class PaginatedResponse(BaseModel):
    total: int
    limit: int
    offset: int


class AgentList(PaginatedResponse):
    agents: list[Agent]


class SessionList(PaginatedResponse):
    sessions: list[Session]


class TranscriptList(BaseModel):
    transcripts: list[Transcript]
    session_id: uuid.UUID
