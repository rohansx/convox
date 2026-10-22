import asyncio
import base64
import json
import logging

import numpy as np

from convox.audio.frames import FRAME_BYTES
from convox.legs.base import LegTransport
from convox.legs.exotel import ExotelLeg
from convox.legs.ws import MemoryWS, ws_pair


def start_msg(rate: str | None = "16000", sid: str = "ST1") -> str:
    start: dict = {
        "stream_sid": sid,
        "call_sid": "CA1",
        "account_sid": "AC1",
        "from": "+919876543210",
        "to": "+911234567890",
        "custom_parameters": {"call": "c_91", "side": "a"},
    }
    if rate is not None:
        start["media_format"] = {"encoding": "raw", "sample_rate": rate, "bit_rate": "256"}
    return json.dumps({"event": "start", "sequence_number": 1, "stream_sid": sid, "start": start})


def media_msg(pcm: bytes, chunk: int = 1) -> str:
    return json.dumps(
        {
            "event": "media",
            "sequence_number": chunk + 1,
            "stream_sid": "ST1",
            "media": {
                "chunk": chunk,
                "timestamp": str(chunk * 100),
                "payload": base64.b64encode(pcm).decode(),
            },
        }
    )


def stop_msg() -> str:
    return json.dumps(
        {
            "event": "stop",
            "sequence_number": 99,
            "stream_sid": "ST1",
            "stop": {"call_sid": "CA1", "account_sid": "AC1", "reason": "stopped"},
        }
    )


def sent_events(client: MemoryWS) -> list[dict]:
    return [json.loads(m) for m in client.drain()]


async def settle() -> None:
    for _ in range(25):
        await asyncio.sleep(0)


async def started(rate: str = "16000", **kw) -> tuple[ExotelLeg, MemoryWS, asyncio.Task]:
    server, client = ws_pair()
    leg = ExotelLeg("a", server, **kw)
    task = asyncio.create_task(leg.run())
    await client.send_text(json.dumps({"event": "connected"}))
    await client.send_text(start_msg(rate))
    await settle()
    return leg, client, task


async def finish(client: MemoryWS, task: asyncio.Task) -> None:
    await client.send_text(stop_msg())
    await asyncio.wait_for(task, 1)


def test_satisfies_protocol():
    server, _ = ws_pair()
    assert isinstance(ExotelLeg("a", server), LegTransport)


async def test_start_sets_stream_ids_and_rate():
    leg, client, task = await started("16000")
    assert leg.stream_sid == "ST1"
    assert leg.call_sid == "CA1"
    assert leg.sample_rate == 16000
    assert leg.custom_parameters == {"call": "c_91", "side": "a"}
    assert leg.started.is_set()
    await finish(client, task)


async def test_media_base64_decoded_into_640_byte_frames_and_stop_ends_frames():
    leg, client, task = await started("16000")
    pcm = bytes(range(256)) * 25  # 6400 bytes = 200 ms at 16k
    await client.send_text(media_msg(pcm[:3200], 1))
    await client.send_text(media_msg(pcm[3200:], 2))
    await finish(client, task)
    frames = [f async for f in leg.frames()]
    assert [len(f) for f in frames] == [FRAME_BYTES] * 10
    assert b"".join(frames) == pcm
    assert leg.closed and client.peer is not None and client.peer.closed
    assert leg.close_reason == "stopped"


async def test_8k_start_resamples_inbound_and_logs_once(caplog):
    caplog.set_level(logging.WARNING, logger="convox.legs.exotel")
    leg, client, task = await started("8000")
    assert leg.sample_rate == 8000
    t = np.arange(8000) / 8000
    pcm8 = (np.sin(2 * np.pi * 300 * t) * 8000).astype(np.int16).tobytes()  # 1 s
    for i in range(10):
        await client.send_text(media_msg(pcm8[i * 1600 : (i + 1) * 1600], i + 1))
    await finish(client, task)
    total = sum([len(f) async for f in leg.frames()])
    assert abs(total - 32000) <= 2 * FRAME_BYTES
    assert [r.getMessage() for r in caplog.records].count("leg.resample") == 1


async def test_missing_media_format_defaults_to_8k():
    leg, client, task = await started(None)  # type: ignore[arg-type]
    assert leg.sample_rate == 8000
    await finish(client, task)


async def test_send_flushes_100ms_chunks_and_mark_flushes_padded_tail():
    leg, client, task = await started("16000")
    await leg.send(b"\x01\x00" * (640 * 11 // 2))  # 7040 bytes
    events = sent_events(client)
    assert [e["event"] for e in events] == ["media", "media"]
    assert all(e["stream_sid"] == "ST1" for e in events)
    sizes = [len(base64.b64decode(e["media"]["payload"])) for e in events]
    assert sizes == [3200, 3200]
    await leg.mark("a:1")
    events = sent_events(client)
    assert [e["event"] for e in events] == ["media", "mark"]
    tail = base64.b64decode(events[0]["media"]["payload"])
    assert len(tail) == 640 and len(tail) % 320 == 0
    assert events[1] == {"event": "mark", "stream_sid": "ST1", "mark": {"name": "a:1"}}
    await finish(client, task)


async def test_8k_outbound_chunks_are_1600_bytes():
    leg, client, task = await started("8000")
    await leg.send(b"\x00\x10" * 16000)  # 1 s at 16k
    sizes = [len(base64.b64decode(e["media"]["payload"])) for e in sent_events(client)]
    assert sizes and all(s == 1600 for s in sizes)
    assert all(s % 320 == 0 for s in sizes)
    await finish(client, task)


async def test_send_before_start_is_buffered_until_start():
    server, client = ws_pair()
    leg = ExotelLeg("b", server)
    task = asyncio.create_task(leg.run())
    await leg.send(b"\x00" * 6400)
    assert client.drain() == []
    await client.send_text(start_msg("16000"))
    await settle()
    events = sent_events(client)
    assert [len(base64.b64decode(e["media"]["payload"])) for e in events] == [3200, 3200]
    await finish(client, task)


async def test_clear_sends_clear_and_drops_local_buffer():
    leg, client, task = await started("16000")
    await leg.send(b"\x00" * 1280)  # below one chunk: stays buffered
    assert client.drain() == []
    await leg.clear()
    assert sent_events(client) == [{"event": "clear", "stream_sid": "ST1"}]
    await leg.mark("after")
    assert [e["event"] for e in sent_events(client)] == ["mark"]
    await finish(client, task)


async def test_mark_round_trip_fires_on_mark_and_cleared_marks_do_not():
    leg, client, task = await started("16000")
    fired: list[str] = []
    leg.on_mark(fired.append)
    await leg.send(b"\x00" * 3200)
    await leg.mark("a:1")
    await leg.mark("a:2")
    await leg.clear()
    await leg.mark("a:3")
    for name in ("a:1", "a:3", "unknown"):
        await client.send_text(json.dumps({"event": "mark", "mark": {"name": name}}))
    await settle()
    assert fired == ["a:3"]
    await finish(client, task)


async def test_dtmf_fires_on_dtmf():
    leg, client, task = await started("16000")
    digits: list[str] = []
    leg.on_dtmf(digits.append)
    await client.send_text(
        json.dumps(
            {"event": "dtmf", "stream_sid": "ST1", "dtmf": {"duration": "200", "digit": "5"}}
        )
    )
    await settle()
    assert digits == ["5"]
    await finish(client, task)


async def test_chunk_gap_reported():
    leg, client, task = await started("16000")
    gaps: list[int] = []
    leg.on_gap(gaps.append)
    await client.send_text(media_msg(b"\x00" * 3200, 1))
    await client.send_text(media_msg(b"\x00" * 3200, 4))
    await settle()
    assert gaps == [200]
    await finish(client, task)


async def test_bad_json_and_unknown_events_are_ignored():
    leg, client, task = await started("16000")
    await client.send_text("not json")
    await client.send_text(json.dumps({"event": "mystery"}))
    await client.send_text(media_msg(b"\x00" * 640, 1))
    await finish(client, task)
    assert len([f async for f in leg.frames()]) == 1


async def test_disconnect_without_stop_ends_run_and_frames():
    leg, client, task = await started("16000")
    await client.close()
    await asyncio.wait_for(task, 1)
    assert [f async for f in leg.frames()] == []
    assert leg.close_reason == "disconnect"


async def test_close_is_idempotent_and_send_after_close_is_noop():
    leg, client, task = await started("16000")
    await leg.close()
    await leg.close()
    await asyncio.wait_for(task, 1)
    await leg.send(b"\x00" * 6400)
    await leg.mark("x")
    await leg.clear()
