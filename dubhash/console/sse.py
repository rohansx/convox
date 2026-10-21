"""Server-sent events for the console, fed straight from the EventBus (never the DB).

Each event is `event: <kind>\\ndata: <json>\\n\\n`. The JSON is the CallEvent payload plus
`call_id` and `ts_ms` (payload keys win on a clash), so the all-calls stream can be
demultiplexed by the page.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
from collections.abc import AsyncIterator
from typing import Any

from dubhash.events import EventBus
from dubhash.types import CallEvent

KEEPALIVE_S = 15.0
PING = ": ping\n\n"
CONNECTED = ": connected\n\n"


def _default(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}
    if isinstance(obj, set | frozenset | tuple):
        return list(obj)
    return str(obj)


def event_data(ev: CallEvent) -> dict[str, Any]:
    return {"call_id": ev.call_id, "ts_ms": ev.ts_ms, **ev.payload}


def format_event(ev: CallEvent) -> str:
    data = json.dumps(event_data(ev), ensure_ascii=False, default=_default)
    return f"event: {ev.kind}\ndata: {data}\n\n"


async def sse_stream(
    bus: EventBus, call_id: str | None, *, keepalive_s: float = KEEPALIVE_S
) -> AsyncIterator[str]:
    """Yields a `: connected` comment once subscribed, then events for `call_id` (all
    calls if None), with a `: ping` comment after every `keepalive_s` of silence."""
    sub = bus.subscribe(call_id)
    nxt: asyncio.Future[CallEvent] = asyncio.ensure_future(anext(sub))
    try:
        await asyncio.sleep(0)  # let the subscription register before we say "connected"
        yield CONNECTED
        while True:
            done, _ = await asyncio.wait({nxt}, timeout=keepalive_s)
            if not done:
                yield PING
                continue
            ev = nxt.result()
            nxt = asyncio.ensure_future(anext(sub))
            yield format_event(ev)
    finally:
        nxt.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
            await nxt
        await sub.aclose()
