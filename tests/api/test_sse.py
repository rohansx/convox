import asyncio
import json

from dubhash.console.sse import format_event, sse_stream
from dubhash.events import EventBus
from dubhash.types import CallEvent, Turn


def parse(chunk: str) -> tuple[str, dict]:
    lines = chunk.rstrip("\n").split("\n")
    assert lines[0].startswith("event: ") and lines[1].startswith("data: ")
    return lines[0][7:], json.loads(lines[1][6:])


def test_format_event_shape():
    ev = CallEvent("c_1", 1200, "dtmf", {"side": "a", "digit": "1"})
    s = format_event(ev)
    assert s.endswith("\n\n") and s.count("\n") == 3
    kind, data = parse(s)
    assert kind == "dtmf"
    assert data == {"side": "a", "digit": "1", "call_id": "c_1", "ts_ms": 1200}


def test_format_event_encodes_dataclasses_and_unicode():
    t = Turn(call_id="c_1", side="b", seq=2, src_text="ಸರಿ\nನೋಡಿ", tgt_text="theek hai")
    s = format_event(CallEvent("c_1", 5, "turn", {"turn": t}))
    assert s.count("\n") == 3  # embedded newline stays escaped inside the JSON
    assert "ಸರಿ" in s
    _, data = parse(s)
    assert data["turn"]["seq"] == 2 and data["turn"]["src_text"] == "ಸರಿ\nನೋಡಿ"
    assert data["turn"]["t_play_start"] is None


async def test_stream_yields_published_events_for_its_call():
    bus = EventBus()
    gen = sse_stream(bus, "c_1")
    first = await asyncio.wait_for(anext(gen), 1)
    assert first.startswith(":")  # connected comment, subscription is live
    assert bus.subscriber_count == 1
    bus.publish(CallEvent("c_2", 1, "state", {"to": "IDLE"}))
    bus.publish(CallEvent("c_1", 2, "state", {"to": "A_SPEAKING", "queue_depth": 0}))
    kind, data = parse(await asyncio.wait_for(anext(gen), 1))
    assert kind == "state" and data["call_id"] == "c_1" and data["to"] == "A_SPEAKING"
    await gen.aclose()
    assert bus.subscriber_count == 0


async def test_stream_without_call_gets_all_calls():
    bus = EventBus()
    gen = sse_stream(bus, None)
    await anext(gen)
    bus.publish(CallEvent("c_9", 1, "call", {"status": "ringing"}))
    kind, data = parse(await asyncio.wait_for(anext(gen), 1))
    assert kind == "call" and data["call_id"] == "c_9"
    await gen.aclose()


async def test_keepalive_comment_when_idle():
    bus = EventBus()
    gen = sse_stream(bus, "c_1", keepalive_s=0.02)
    await anext(gen)
    assert await asyncio.wait_for(anext(gen), 1) == ": ping\n\n"
    # an event after a ping still arrives (the pending read survived the timeout)
    bus.publish(CallEvent("c_1", 3, "gap", {"side": "a", "gap_ms": 2000}))
    while (chunk := await asyncio.wait_for(anext(gen), 1)) == ": ping\n\n":
        pass
    assert parse(chunk)[0] == "gap"
    await gen.aclose()
    assert bus.subscriber_count == 0
