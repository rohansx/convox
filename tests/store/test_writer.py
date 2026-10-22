import asyncio
import logging

import pytest
from sqlalchemy import func, select

from convox.store.db import init_db, make_engine, make_sessionmaker
from convox.store.models import CallRow, EventRow, TurnRow
from convox.store.writer import TurnWriter, first_audio_ms, percentile
from convox.types import CallEvent, Turn


@pytest.fixture
async def sm(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/w.db")
    await init_db(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


def turn(seq: int, *, side="a", speech_end=1000, play_start=2500, **kw) -> Turn:
    return Turn(
        call_id="c_1",
        side=side,
        seq=seq,
        t_speech_end=speech_end,
        t_play_start=play_start,
        src_text=f"src {seq}",
        tgt_text=f"tgt {seq}",
        **kw,
    )


async def count(sm, model) -> int:
    async with sm() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def started(sm, **kw) -> TurnWriter:
    w = TurnWriter(sm, **kw)
    w.upsert_call("c_1", provider="fake", mode="interpret", a_lang="hi-IN", b_lang="kn-IN")
    w.start()
    return w


# --- pure helpers ---------------------------------------------------------------------


def test_first_audio_ms():
    assert first_audio_ms(turn(1, speech_end=7450, play_start=8950)) == 1500
    assert first_audio_ms(turn(1, play_start=None)) is None
    assert first_audio_ms(turn(1, speech_end=None)) is None


def test_percentile_nearest_rank():
    assert percentile([], 50) is None
    assert percentile([1500], 95) == 1500
    vals = list(range(100, 2100, 100))  # 20 values: 100..2000
    assert percentile(vals, 50) == 1000
    assert percentile(vals, 95) == 1900
    assert percentile([3, 1, 2], 50) == 2


# --- writing --------------------------------------------------------------------------


async def test_turns_are_batch_inserted(sm):
    w = await started(sm, batch_size=3, flush_ms=10)
    for i in range(1, 8):
        w.enqueue_turn(turn(i), record=True)
    await w.drain()
    assert await count(sm, TurnRow) == 7
    assert w.written == 7 and w.dropped == 0
    assert w.batches >= 3  # 7 rows with batch_size=3 cannot fit in fewer than 3
    async with sm() as s:
        row = (await s.execute(select(TurnRow).where(TurnRow.seq == 1))).scalar_one()
    assert row.src_text == "src 1" and row.tgt_text == "tgt 1"
    assert row.t_play_start == 2500 and row.side == "a"
    await w.close()


async def test_record_false_nulls_text_before_insert(sm):
    w = await started(sm, flush_ms=10)
    t = turn(1)
    w.enqueue_turn(t, record=False)
    await w.drain()
    async with sm() as s:
        row = (await s.execute(select(TurnRow))).scalar_one()
    assert row.src_text is None and row.tgt_text is None
    assert row.t_speech_end == 1000  # timings kept
    assert t.src_text == "src 1"  # caller's object untouched (console still shows it)
    await w.close()


async def test_enqueue_drops_when_full_and_never_raises(sm):
    w = TurnWriter(sm, maxsize=3)  # not started: nothing drains
    for i in range(1, 6):
        w.enqueue_turn(turn(i), record=False)
    w.enqueue_event(CallEvent("c_1", 10, "dtmf", {"side": "a", "digit": "1"}))
    assert w.dropped == 3
    assert w.queue_depth == 3


async def test_events_are_inserted_and_text_kinds_skipped(sm):
    w = await started(sm, flush_ms=10)
    assert w.enqueue_event(CallEvent("c_1", 1200, "dtmf", {"side": "a", "digit": "1"}))
    assert w.enqueue_event(CallEvent("c_1", 1300, "barge_in", {"side": "b", "clipped_ms": 840}))
    # partial/turn carry transcript text (retention), state is per-change noise
    assert not w.enqueue_event(CallEvent("c_1", 1400, "partial", {"side": "a", "text": "x"}))
    assert not w.enqueue_event(CallEvent("c_1", 1400, "turn", {"turn": {}}))
    assert not w.enqueue_event(CallEvent("c_1", 1400, "state", {"to": "IDLE"}))
    await w.drain()
    async with sm() as s:
        rows = (await s.execute(select(EventRow).order_by(EventRow.id))).scalars().all()
    assert [r.kind for r in rows] == ["dtmf", "barge_in"]
    assert rows[0].payload == {"side": "a", "digit": "1"}
    assert rows[0].ts_ms == 1200 and rows[0].ts is not None
    await w.close()


async def test_upsert_call_inserts_then_updates(sm):
    w = await started(sm, flush_ms=10)
    w.upsert_call("c_1", status="in_progress", record=True)
    await w.drain()
    async with sm() as s:
        row = (await s.execute(select(CallRow))).scalar_one()
    assert row.status == "in_progress" and row.record is True
    assert row.provider == "fake" and row.a_lang == "hi-IN"
    await w.close()


async def test_finish_call_writes_summary_from_turns(sm):
    w = await started(sm, flush_ms=10)
    fa = [1300, 1500, 1500, 1700, 2100]
    for i, ms in enumerate(fa, start=1):
        w.enqueue_turn(turn(i, speech_end=1000, play_start=1000 + ms), record=False)
    w.enqueue_turn(turn(6, backchannel=True, play_start=None), record=False)
    w.enqueue_event(CallEvent("c_1", 9000, "bypass", {"side": "b", "digit": "0"}))
    w.finish_call("c_1", duration_s=95, interpreted_s=80)
    await w.drain()
    async with sm() as s:
        row = (await s.execute(select(CallRow))).scalar_one()
    assert row.status == "completed"
    assert row.duration_s == 95 and row.interpreted_s == 80
    assert row.turns == 5  # backchannel excluded
    assert row.p50_first_audio_ms == 1500
    assert row.p95_first_audio_ms == 2100
    assert row.escape_used is True  # derived from the bypass event
    assert row.ended_at is not None
    await w.close()


async def test_finish_call_returns_summary_and_accepts_explicit_turns(sm):
    w = TurnWriter(sm)
    turns = [turn(1, play_start=2000), turn(2, play_start=3000)]
    summary = w.finish_call("c_9", turns=turns, escape_used=False, duration_s=10)
    assert summary["turns"] == 2
    assert summary["p50_first_audio_ms"] == 1000
    assert summary["p95_first_audio_ms"] == 2000
    assert summary["escape_used"] is False


# --- failure handling -----------------------------------------------------------------


class FlakySessionmaker:
    """Raises on the first `fail` session opens, then delegates."""

    def __init__(self, real, fail: int) -> None:
        self.real, self.fail, self.calls = real, fail, 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.fail:
            raise ConnectionError("db down")
        return self.real()


async def test_db_error_retries_once_then_succeeds(sm):
    flaky = FlakySessionmaker(sm, fail=1)
    w = TurnWriter(flaky, flush_ms=10, retry_delay_s=0)
    w.start()
    w.enqueue_turn(turn(1), record=False)
    await w.drain()
    assert await count(sm, TurnRow) == 1
    assert w.written == 1 and w.dropped == 0
    await w.close()


async def test_db_error_twice_drops_batch_and_keeps_running(sm, caplog):
    flaky = FlakySessionmaker(sm, fail=2)
    w = TurnWriter(flaky, flush_ms=10, retry_delay_s=0)
    w.start()
    with caplog.at_level(logging.WARNING, logger="convox.store.writer"):
        w.enqueue_turn(turn(1), record=False)
        await w.drain()
    assert w.dropped == 1 and w.written == 0
    assert any("store.batch_dropped" in r.getMessage() for r in caplog.records)
    # still alive: the next batch goes through
    w.upsert_call("c_1", provider="fake", mode="interpret", a_lang="hi-IN", b_lang="kn-IN")
    w.enqueue_turn(turn(2), record=False)
    await w.drain()
    assert await count(sm, TurnRow) == 1
    await w.close()


async def test_close_drains_pending_rows(sm):
    w = await started(sm, flush_ms=10_000, batch_size=1000)
    for i in range(1, 4):
        w.enqueue_turn(turn(i), record=False)
    await asyncio.wait_for(w.close(), timeout=2)
    assert await count(sm, TurnRow) == 3


async def test_chunk_fields_are_stored(sm):
    """L9: utterance_id / chunk / t_utt_start are turns columns (nullable)."""
    w = await started(sm, flush_ms=10)
    w.enqueue_turn(turn(1, utterance_id=3, chunk=1, t_utt_start=10), record=True)
    await w.drain()
    async with sm() as s:
        row = (await s.execute(select(TurnRow))).scalar_one()
    assert (row.utterance_id, row.chunk, row.t_utt_start) == (3, 1, 10)
    await w.close()
