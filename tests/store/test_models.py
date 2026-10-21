import dataclasses

from sqlalchemy import inspect, select

from dubhash.store.db import init_db, make_engine, make_sessionmaker
from dubhash.store.models import CallRow, EventRow, LegRow, TurnRow
from dubhash.types import Turn


def test_turn_columns_match_turn_dataclass():
    cols = {c.name for c in TurnRow.__table__.columns}
    fields = {f.name for f in dataclasses.fields(Turn)}
    assert fields <= cols
    assert cols - fields == {"id"}


def test_table_names():
    assert CallRow.__tablename__ == "calls"
    assert LegRow.__tablename__ == "legs"
    assert TurnRow.__tablename__ == "turns"
    assert EventRow.__tablename__ == "events"


def test_call_columns_from_data_model():
    cols = {c.name for c in CallRow.__table__.columns}
    expected = {
        "id", "created_at", "provider", "mode", "a_number", "a_lang", "b_number", "b_lang",
        "status", "started_at", "ended_at", "duration_s", "interpreted_s", "turns",
        "p50_first_audio_ms", "p95_first_audio_ms", "escape_used", "webhook", "record",
        "platform_id",
    }  # fmt: skip
    assert expected <= cols


def test_leg_and_event_columns():
    legs = {c.name for c in LegRow.__table__.columns}
    assert {
        "call_id", "side", "provider_call_sid", "stream_sid", "sample_rate",
        "answered_at", "closed_at", "close_reason",
    } <= legs  # fmt: skip
    events = {c.name for c in EventRow.__table__.columns}
    assert {"id", "call_id", "ts", "kind", "payload"} <= events


async def test_init_db_creates_tables_on_sqlite(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await init_db(engine)
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert {"calls", "legs", "turns", "events"} <= names

    sm = make_sessionmaker(engine)
    async with sm() as s, s.begin():
        s.add(CallRow(id="c_1", provider="fake", mode="interpret", a_lang="hi-IN",
                      b_lang="kn-IN", status="ringing"))  # fmt: skip
    async with sm() as s:
        row = (await s.execute(select(CallRow))).scalar_one()
    assert row.turns == 0 and row.escape_used is False and row.record is False
    assert row.created_at is not None
    await engine.dispose()
