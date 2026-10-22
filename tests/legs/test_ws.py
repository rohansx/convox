import asyncio

import pytest
from starlette.websockets import WebSocket

from convox.audio.frames import FRAME_BYTES
from convox.clock import FakeClock, MonotonicClock
from convox.legs.ws import (
    ChunkGapDetector,
    InboundAudio,
    OutboundAudio,
    PlayoutTracker,
    WSClosed,
    WSLike,
    ws_pair,
)


async def test_ws_pair_text_and_bytes_round_trip():
    server, client = ws_pair()
    await client.send_text("hi")
    await client.send_bytes(b"\x01\x02")
    assert await server.receive_text() == "hi"
    assert await server.receive_bytes() == b"\x01\x02"
    await server.send_text("yo")
    assert client.drain() == ["yo"]
    assert client.drain() == []


async def test_ws_pair_receive_mixed_and_disconnect():
    server, client = ws_pair()
    await client.send_bytes(b"ab")
    await client.send_text("{}")
    await client.close()
    assert await server.receive() == {"type": "websocket.receive", "bytes": b"ab"}
    assert await server.receive() == {"type": "websocket.receive", "text": "{}"}
    assert (await server.receive())["type"] == "websocket.disconnect"


async def test_ws_pair_receive_text_raises_after_peer_close():
    server, client = ws_pair()
    await client.close()
    with pytest.raises(WSClosed):
        await server.receive_text()
    with pytest.raises(WSClosed):
        await client.send_text("late")
    assert client.closed


def test_starlette_websocket_satisfies_wslike():
    async def receive():  # pragma: no cover
        return {}

    async def send(_):  # pragma: no cover
        return None

    ws = WebSocket({"type": "websocket", "path": "/", "headers": []}, receive, send)
    assert isinstance(ws, WSLike)
    server, _ = ws_pair()
    assert isinstance(server, WSLike)


async def test_inbound_audio_reframes_and_ends():
    inb = InboundAudio()
    inb.push(b"\x01" * 1000)
    inb.push(b"\x02" * 600)
    inb.end()
    frames = [f async for f in inb.frames()]
    assert [len(f) for f in frames] == [FRAME_BYTES] * 3
    assert frames[2].endswith(b"\x00")


async def test_inbound_audio_resamples_8k_to_16k():
    inb = InboundAudio()
    inb.set_rate(8000)
    inb.push(b"\x00\x10" * 8000)  # 1 s at 8k
    inb.end()
    n = sum([len(f) async for f in inb.frames()])
    assert abs(n - 32000) <= 2 * FRAME_BYTES


async def test_inbound_audio_frames_waits_for_push():
    inb = InboundAudio()

    async def later():
        await asyncio.sleep(0)
        inb.push(b"\x00" * FRAME_BYTES)
        inb.end()

    task = asyncio.get_running_loop().create_task(later())
    assert [len(f) async for f in inb.frames()] == [FRAME_BYTES]
    await task


async def test_inbound_audio_push_after_end_is_ignored():
    inb = InboundAudio()
    inb.end()
    inb.push(b"\x00" * FRAME_BYTES)
    inb.end()
    assert [f async for f in inb.frames()] == []


def test_outbound_audio_chunks_100ms_at_16k():
    out = OutboundAudio(chunk_ms=100)
    chunks = out.add(b"\x00" * 7000)
    assert [len(c) for c in chunks] == [3200, 3200]
    tail = out.flush_final()
    assert tail is not None and len(tail) == 640 and len(tail) % 320 == 0
    assert out.flush_final() is None


def test_outbound_audio_resamples_to_8k_wire():
    out = OutboundAudio(chunk_ms=100)
    out.set_rate(8000)
    chunks = out.add(b"\x00\x10" * 16000)  # 1 s at 16k
    tail = out.flush_final()
    assert all(len(c) == 1600 for c in chunks)
    total = sum(len(c) for c in chunks) + (len(tail) if tail else 0)
    assert abs(total - 16000) <= 640
    assert tail is None or len(tail) % 320 == 0


def test_outbound_audio_clear_drops_buffer():
    out = OutboundAudio(chunk_ms=100)
    out.add(b"\x00" * 1000)
    out.clear()
    assert out.flush_final() is None


def test_outbound_audio_rejects_bad_chunk_size():
    with pytest.raises(ValueError):
        OutboundAudio(chunk_ms=15)


def test_gap_detector():
    g = ChunkGapDetector()
    assert g.observe(1, 100) == 0
    assert g.observe(2, 100) == 0
    assert g.observe(5, 100) == 200
    assert g.observe(None, 100) == 0
    assert g.observe(6, 100) == 0
    assert g.observe("7", 100) == 0
    assert g.observe("9", 100) == 100


async def test_playout_tracker_fires_marks_on_clock():
    clock = FakeClock()
    fired: list[str] = []
    t = PlayoutTracker(clock, fired.append, autopoll=False)
    t.played(3200)  # 100 ms
    t.mark("m1")
    t.played(6400)  # 200 ms more
    t.mark("m2")
    clock.advance(99)
    t.poll()
    assert fired == []
    clock.advance(1)
    t.poll()
    assert fired == ["m1"]
    clock.advance(200)
    t.poll()
    assert fired == ["m1", "m2"]


async def test_playout_tracker_immediate_mark_uses_call_soon():
    fired: list[str] = []
    t = PlayoutTracker(FakeClock(), fired.append, autopoll=False)
    t.mark("now")
    assert fired == []
    await asyncio.sleep(0)
    assert fired == ["now"]


async def test_playout_tracker_clear_drops_pending_and_reports_unplayed():
    clock = FakeClock()
    fired: list[str] = []
    t = PlayoutTracker(clock, fired.append, autopoll=False)
    t.played(32000)  # 1 s
    t.mark("m")
    clock.advance(250)
    assert t.clear() == 24000
    clock.advance(2000)
    t.poll()
    await asyncio.sleep(0)
    assert fired == []
    assert t.unplayed_bytes() == 0


async def test_playout_tracker_restarts_from_now_after_idle():
    clock = FakeClock()
    t = PlayoutTracker(clock, lambda _: None, autopoll=False)
    t.played(3200)
    clock.advance(500)
    t.played(3200)
    assert t.unplayed_bytes() == 3200


async def test_playout_tracker_autopoll_real_time():
    fired: list[str] = []
    t = PlayoutTracker(MonotonicClock(), fired.append)
    t.played(640)  # 20 ms
    t.mark("rt")
    await asyncio.sleep(0.06)
    assert fired == ["rt"]
    t.close()
