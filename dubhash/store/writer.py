"""Background turn/event writer. The hot path calls the sync `enqueue_*`/`upsert_call`/
`finish_call` methods, which never block or raise; one `run()` task drains the queue and
batch-inserts. A full queue or a DB outage costs DB rows, never call audio.

Retention: with `record=False`, `src_text`/`tgt_text` are nulled before insert, so text
never reaches the database (the stricter option in data-model.md).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, update

from dubhash.store.models import CallRow, EventRow, TurnRow
from dubhash.types import CallEvent, Turn

log = logging.getLogger(__name__)

# Event kinds that become `events` rows (the audit log). `turn` and `partial` carry
# transcript text and would bypass retention; `state` is per-change noise.
PERSISTED_EVENT_KINDS = frozenset(
    {"dtmf", "barge_in", "bypass", "gap", "provider_error", "leg", "call"}
)

_STOP = object()


def first_audio_ms(turn: Turn) -> int | None:
    if turn.t_play_start is None or turn.t_speech_end is None:
        return None
    return turn.t_play_start - turn.t_speech_end


def percentile(values: Iterable[int], p: float) -> int | None:
    """Nearest-rank percentile; None for no values."""
    vals = sorted(values)
    if not vals:
        return None
    rank = max(1, math.ceil(p / 100 * len(vals)))
    return vals[rank - 1]


def summarize(turns: Iterable[Turn]) -> dict[str, int | None]:
    stats = _CallStats()
    for t in turns:
        stats.add(t)
    return stats.summary()


def jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set | frozenset):
        return [jsonable(v) for v in obj]
    if isinstance(obj, str | int | float | bool) or obj is None:
        return obj
    return str(obj)


@dataclass(slots=True)
class _CallStats:
    """Running per-call aggregates. Keeps numbers only, never transcript text."""

    turns: int = 0
    first_audio: list[int] = field(default_factory=list)
    escape_used: bool = False

    def add(self, turn: Turn) -> None:
        if turn.backchannel:
            return
        self.turns += 1
        if (ms := first_audio_ms(turn)) is not None:
            self.first_audio.append(ms)

    def summary(self) -> dict[str, int | None]:
        return {
            "turns": self.turns,
            "p50_first_audio_ms": percentile(self.first_audio, 50),
            "p95_first_audio_ms": percentile(self.first_audio, 95),
        }


class TurnWriter:
    def __init__(
        self,
        sessionmaker: Callable[[], Any],
        *,
        maxsize: int = 10000,
        batch_size: int = 100,
        flush_ms: int = 500,
        retry_delay_s: float = 0.5,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._q: asyncio.Queue[Any] = asyncio.Queue(maxsize)
        self._batch_size = batch_size
        self._flush_s = flush_ms / 1000
        self._retry_delay_s = retry_delay_s
        self._stats: dict[str, _CallStats] = {}
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self.dropped = 0
        self.written = 0
        self.batches = 0

    # --- hot-path API: sync, never blocks, never raises --------------------------------

    def enqueue_turn(self, turn: Turn, record: bool) -> bool:
        try:
            self._stats.setdefault(turn.call_id, _CallStats()).add(turn)
            row = dataclasses.asdict(turn)
            if not record:
                row["src_text"] = None
                row["tgt_text"] = None
            return self._put(("turn", row))
        except Exception:
            log.exception("store.enqueue_failed")
            return False

    def enqueue_event(self, ev: CallEvent) -> bool:
        try:
            if ev.kind == "bypass":
                self._stats.setdefault(ev.call_id, _CallStats()).escape_used = True
            if ev.kind not in PERSISTED_EVENT_KINDS:
                return False
            row = {
                "call_id": ev.call_id,
                "ts": datetime.now(UTC),
                "ts_ms": ev.ts_ms,
                "kind": ev.kind,
                "payload": jsonable(ev.payload),
            }
            return self._put(("event", row))
        except Exception:
            log.exception("store.enqueue_failed")
            return False

    def upsert_call(
        self,
        call_id: str,
        *,
        provider: str | None = None,
        mode: str | None = None,
        a_lang: str | None = None,
        b_lang: str | None = None,
        status: str | None = None,
        a_number: str | None = None,
        b_number: str | None = None,
        record: bool | None = None,
        webhook: str | None = None,
        platform_id: str | None = None,
        started_at: datetime | None = None,
    ) -> bool:
        """Insert the calls row (status defaults to "ringing") or update the given fields."""
        fields = {
            "provider": provider,
            "mode": mode,
            "a_lang": a_lang,
            "b_lang": b_lang,
            "status": status,
            "a_number": a_number,
            "b_number": b_number,
            "record": record,
            "webhook": webhook,
            "platform_id": platform_id,
            "started_at": started_at,
        }
        return self._put(
            ("call_upsert", call_id, {k: v for k, v in fields.items() if v is not None})
        )

    def finish_call(
        self,
        call_id: str,
        *,
        status: str = "completed",
        ended_at: datetime | None = None,
        duration_s: int | None = None,
        interpreted_s: int | None = None,
        escape_used: bool | None = None,
        turns: Sequence[Turn] | None = None,
    ) -> dict[str, Any]:
        """Queue the end-of-call summary and return it. p50/p95 first-audio and the turn
        count come from `turns` if given, else from every turn enqueued for this call
        (including ones the queue dropped). `escape_used` defaults to "a bypass event was
        seen"."""
        stats = self._stats.pop(call_id, _CallStats())
        summary: dict[str, Any] = {
            "status": status,
            "ended_at": ended_at or datetime.now(UTC),
            "duration_s": duration_s,
            "interpreted_s": interpreted_s,
            "escape_used": stats.escape_used if escape_used is None else escape_used,
            **(stats.summary() if turns is None else summarize(turns)),
        }
        self._put(("call_finish", call_id, summary))
        return summary

    @property
    def queue_depth(self) -> int:
        return self._q.qsize()

    def _put(self, item: Any) -> bool:
        try:
            self._q.put_nowait(item)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 1000 == 0:
                log.warning("store.queue_full", extra={"dropped": self.dropped})
            return False

    # --- background task ---------------------------------------------------------------

    def start(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._closing = False
            self._task = asyncio.create_task(self.run(), name="store.writer")
        return self._task

    async def drain(self) -> None:
        """Wait until everything queued so far has been written (or dropped)."""
        await self._q.join()

    async def close(self) -> None:
        """Write everything still queued, then stop."""
        self._closing = True
        try:
            self._q.put_nowait(_STOP)
        except asyncio.QueueFull:
            pass  # the loop is busy anyway and will see _closing
        if self._task is not None and not self._task.done():
            await self._task
        else:
            await self.run()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            if self._closing and self._q.empty():
                return
            batch = [await self._q.get()]
            deadline = loop.time() + self._flush_s
            while len(batch) < self._batch_size and batch[-1] is not _STOP:
                try:
                    batch.append(self._q.get_nowait())
                    continue
                except asyncio.QueueEmpty:
                    pass
                remaining = deadline - loop.time()
                if self._closing or remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self._q.get(), remaining))
                except TimeoutError:
                    break
            try:
                items = [i for i in batch if i is not _STOP]
                if items:
                    await self._write(items)
            finally:
                for _ in batch:
                    self._q.task_done()

    async def _write(self, items: list[Any]) -> None:
        for attempt in (1, 2):
            try:
                await self._write_once(items)
            except Exception as e:  # noqa: BLE001 - storage must never take the call down
                log.warning("store.write_failed", extra={"attempt": attempt, "error": repr(e)})
                if attempt == 1 and self._retry_delay_s:
                    await asyncio.sleep(self._retry_delay_s)
                continue
            self.batches += 1
            self.written += sum(1 for i in items if i[0] in ("turn", "event"))
            return
        self.dropped += len(items)
        log.warning("store.batch_dropped", extra={"rows": len(items)})

    async def _write_once(self, items: list[Any]) -> None:
        async with self._sessionmaker() as s, s.begin():
            run: list[dict[str, Any]] = []
            run_kind: str | None = None

            async def flush_run() -> None:
                if run:
                    model = TurnRow if run_kind == "turn" else EventRow
                    await s.execute(insert(model), list(run))
                    run.clear()

            for item in items:
                kind = item[0]
                if kind in ("turn", "event"):
                    if kind != run_kind:
                        await flush_run()
                        run_kind = kind
                    run.append(item[1])
                    continue
                await flush_run()
                run_kind = None
                call_id, fields = item[1], item[2]
                if kind == "call_upsert":
                    row = await s.get(CallRow, call_id)
                    if row is None:
                        s.add(CallRow(id=call_id, **{"status": "ringing", **fields}))
                    else:
                        for k, v in fields.items():
                            setattr(row, k, v)
                    await s.flush()
                else:  # call_finish
                    await s.execute(update(CallRow).where(CallRow.id == call_id).values(**fields))
            await flush_run()
