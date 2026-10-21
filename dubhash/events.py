"""In-process pub/sub for CallEvents. The controller and interpreters publish; the
console SSE stream and the turn writer subscribe. Publishing never blocks: a slow
subscriber loses events rather than stalling the hot path."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from dubhash.types import CallEvent


class EventBus:
    def __init__(self, maxsize: int = 1000) -> None:
        self._maxsize = maxsize
        self._subs: set[asyncio.Queue[CallEvent]] = set()

    def publish(self, event: CallEvent) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, call_id: str | None = None) -> AsyncIterator[CallEvent]:
        q: asyncio.Queue[CallEvent] = asyncio.Queue(self._maxsize)
        self._subs.add(q)
        try:
            while True:
                ev = await q.get()
                if call_id is None or ev.call_id == call_id:
                    yield ev
        finally:
            self._subs.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)
