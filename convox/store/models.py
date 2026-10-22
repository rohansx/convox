"""SQLAlchemy models for calls, legs, turns and events (docs/tech-spec/data-model.md).

Generic column types only, so tests run on SQLite and prod runs on Postgres. `turns`
column names match `convox.types.Turn` field names exactly. Timestamps on turns are
integer ms relative to the call clock; `calls.started_at` is the anchor.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# bigserial on Postgres, plain INTEGER PRIMARY KEY (rowid alias) on SQLite
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")
JsonType = JSON().with_variant(JSONB(), "postgresql")
Ts = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class CallRow(Base):
    __tablename__ = "calls"
    __table_args__ = (
        CheckConstraint("provider IN ('exotel','plivo','webrtc','fake')", name="calls_provider_ck"),
        CheckConstraint("mode IN ('interpret','bridge')", name="calls_mode_ck"),
        Index("calls_created_at_idx", "created_at"),
        Index("calls_status_idx", "status"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(Ts, server_default=func.now())
    provider: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    a_number: Mapped[str | None] = mapped_column(Text)
    a_lang: Mapped[str] = mapped_column(Text)
    b_number: Mapped[str | None] = mapped_column(Text)
    b_lang: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(Ts)
    ended_at: Mapped[datetime | None] = mapped_column(Ts)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    interpreted_s: Mapped[int | None] = mapped_column(Integer)
    turns: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    p50_first_audio_ms: Mapped[int | None] = mapped_column(Integer)
    p95_first_audio_ms: Mapped[int | None] = mapped_column(Integer)
    escape_used: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    webhook: Mapped[str | None] = mapped_column(Text)
    record: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    platform_id: Mapped[str | None] = mapped_column(Text)


class LegRow(Base):
    __tablename__ = "legs"
    __table_args__ = (
        CheckConstraint("side IN ('a','b')", name="legs_side_ck"),
        Index("legs_provider_call_sid_idx", "provider_call_sid"),
    )

    call_id: Mapped[str] = mapped_column(
        Text, ForeignKey("calls.id", ondelete="CASCADE"), primary_key=True
    )
    side: Mapped[str] = mapped_column(String(1), primary_key=True)
    provider_call_sid: Mapped[str | None] = mapped_column(Text)
    stream_sid: Mapped[str | None] = mapped_column(Text)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    answered_at: Mapped[datetime | None] = mapped_column(Ts)
    closed_at: Mapped[datetime | None] = mapped_column(Ts)
    close_reason: Mapped[str | None] = mapped_column(Text)


class TurnRow(Base):
    __tablename__ = "turns"
    __table_args__ = (
        CheckConstraint("side IN ('a','b')", name="turns_side_ck"),
        UniqueConstraint("call_id", "seq", name="turns_call_seq_uq"),
        Index("turns_call_idx", "call_id", "seq"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(Text, ForeignKey("calls.id", ondelete="CASCADE"))
    side: Mapped[str] = mapped_column(String(1))
    seq: Mapped[int] = mapped_column(Integer)
    t_speech_start: Mapped[int | None] = mapped_column(Integer)
    t_speech_end: Mapped[int | None] = mapped_column(Integer)
    t_eou: Mapped[int | None] = mapped_column(Integer)
    t_stt_final: Mapped[int | None] = mapped_column(Integer)
    t_mt: Mapped[int | None] = mapped_column(Integer)
    t_tts_ttfb: Mapped[int | None] = mapped_column(Integer)
    t_play_start: Mapped[int | None] = mapped_column(Integer)
    t_play_end: Mapped[int | None] = mapped_column(Integer)
    src_text: Mapped[str | None] = mapped_column(Text)
    tgt_text: Mapped[str | None] = mapped_column(Text)
    backchannel: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    clipped_ms: Mapped[int | None] = mapped_column(Integer)
    stt_provider: Mapped[str | None] = mapped_column(Text)
    mt_provider: Mapped[str | None] = mapped_column(Text)
    tts_provider: Mapped[str | None] = mapped_column(Text)
    voice: Mapped[str | None] = mapped_column(Text)
    # L9 chunked interpretation. A database created before these needs
    # ALTER TABLE turns ADD COLUMN utterance_id integer, chunk integer, t_utt_start integer.
    utterance_id: Mapped[int | None] = mapped_column(Integer)
    chunk: Mapped[int | None] = mapped_column(Integer)
    t_utt_start: Mapped[int | None] = mapped_column(Integer)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (Index("events_call_idx", "call_id", "ts"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(Text, ForeignKey("calls.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(Ts)  # wall clock when enqueued
    ts_ms: Mapped[int | None] = mapped_column(Integer)  # call-relative ms (CallEvent.ts_ms)
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
