import asyncio
import base64
import json

import numpy as np

from convox.audio.codec import ulaw_decode, ulaw_encode
from convox.audio.frames import FRAME_BYTES
from convox.legs.base import LegTransport
from convox.legs.plivo import PlivoLeg, parse_media_format
from convox.legs.ws import MemoryWS, ws_pair


def start_msg(encoding: str = "audio/x-l16", rate: int | None = 16000) -> str:
    mf: dict = {"encoding": encoding}
    if rate is not None:
        mf["sampleRate"] = rate
    return json.dumps(
        {
            "event": "start",
            "sequenceNumber": 1,
            "start": {
                "callId": "call-1",
                "streamId": "stream-1",
                "accountId": "MA1",
                "tracks": ["inbound"],
                "mediaFormat": mf,
            },
            "extra_headers": "call=c_91;side=b",
        }
    )


def media_msg(payload: bytes, chunk: int = 1) -> str:
    return json.dumps(
        {
            "event": "media",
            "sequenceNumber": chunk + 1,
            "streamId": "stream-1",
            "media": {
                "track": "inbound",
                "timestamp": str(1705312200000 + chunk * 100),
                "chunk": chunk,
                "payload": base64.b64encode(payload).decode(),
            },
        }
    )


def stop_msg() -> str:
    return json.dumps({"event": "stop", "sequenceNumber": 99, "streamId": "stream-1"})


def sent_events(client: MemoryWS) -> list[dict]:
    return [json.loads(m) for m in client.drain()]


async def settle() -> None:
    for _ in range(25):
        await asyncio.sleep(0)


async def started(encoding="audio/x-l16", rate=16000) -> tuple[PlivoLeg, MemoryWS, asyncio.Task]:
    server, client = ws_pair()
    leg = PlivoLeg("b", server)
    task = asyncio.create_task(leg.run())
    await client.send_text(start_msg(encoding, rate))
    await settle()
    return leg, client, task


async def finish(client: MemoryWS, task: asyncio.Task) -> None:
    await client.send_text(stop_msg())
    await asyncio.wait_for(task, 1)


def test_satisfies_protocol():
    server, _ = ws_pair()
    assert isinstance(PlivoLeg("a", server), LegTransport)


def test_parse_media_format_variants():
    assert parse_media_format({"encoding": "audio/x-mulaw", "sampleRate": 8000}) == ("mulaw", 8000)
    assert parse_media_format({"encoding": "audio/x-l16", "sampleRate": 16000}) == ("l16", 16000)
    assert parse_media_format({"encoding": "audio/x-l16;rate=16000"}) == ("l16", 16000)
    assert parse_media_format({"encoding": "audio/x-mulaw;rate=8000"}) == ("mulaw", 8000)
    assert parse_media_format(None) == ("mulaw", 8000)


async def test_start_sets_ids_and_format():
    leg, client, task = await started()
    assert leg.stream_id == "stream-1"
    assert leg.call_id == "call-1"
    assert leg.stream_sid == "stream-1" and leg.call_sid == "call-1"
    assert leg.sample_rate == 16000 and leg.encoding == "l16"
    assert leg.extra_headers == {"call": "c_91", "side": "b"}
    await finish(client, task)


async def test_l16_media_decoded_into_frames_and_stop_ends_frames():
    leg, client, task = await started()
    pcm = bytes(range(256)) * 25  # 6400 bytes
    await client.send_text(media_msg(pcm[:3200], 1))
    await client.send_text(media_msg(pcm[3200:], 2))
    await finish(client, task)
    frames = [f async for f in leg.frames()]
    assert b"".join(frames) == pcm
    assert all(len(f) == FRAME_BYTES for f in frames)
    assert leg.closed


async def test_mulaw_8k_media_decoded_and_resampled():
    leg, client, task = await started("audio/x-mulaw", 8000)
    t = np.arange(8000) / 8000
    pcm8 = (np.sin(2 * np.pi * 300 * t) * 8000).astype(np.int16).tobytes()
    mu = ulaw_encode(pcm8)
    for i in range(10):
        await client.send_text(media_msg(mu[i * 800 : (i + 1) * 800], i + 1))
    await finish(client, task)
    total = sum([len(f) async for f in leg.frames()])
    assert abs(total - 32000) <= 2 * FRAME_BYTES


async def test_l16_play_audio_chunks_and_checkpoint():
    leg, client, task = await started()
    await leg.send(b"\x00\x01" * 3520)  # 7040 bytes
    events = sent_events(client)
    assert [e["event"] for e in events] == ["playAudio", "playAudio"]
    assert events[0]["media"]["contentType"] == "audio/x-l16"
    assert events[0]["media"]["sampleRate"] == 16000
    assert [len(base64.b64decode(e["media"]["payload"])) for e in events] == [3200, 3200]
    await leg.mark("b:1")
    events = sent_events(client)
    assert [e["event"] for e in events] == ["playAudio", "checkpoint"]
    assert len(base64.b64decode(events[0]["media"]["payload"])) % 320 == 0
    assert events[1] == {"event": "checkpoint", "streamId": "stream-1", "name": "b:1"}
    await finish(client, task)


async def test_mulaw_play_audio_encodes_100ms_at_8k():
    leg, client, task = await started("audio/x-mulaw", 8000)
    await leg.send(b"\x00\x10" * 16000)  # 1 s at 16k
    events = sent_events(client)
    assert events and all(e["media"]["contentType"] == "audio/x-mulaw" for e in events)
    assert all(e["media"]["sampleRate"] == 8000 for e in events)
    payloads = [base64.b64decode(e["media"]["payload"]) for e in events]
    assert all(len(p) == 800 for p in payloads)  # 100 ms of mu-law at 8k
    assert len(ulaw_decode(payloads[0])) == 1600
    await finish(client, task)


async def test_clear_audio_and_checkpoint_round_trip():
    leg, client, task = await started()
    fired: list[str] = []
    leg.on_mark(fired.append)
    await leg.send(b"\x00" * 1280)
    await leg.mark("b:1")
    await leg.clear()
    assert sent_events(client)[-1] == {"event": "clearAudio", "streamId": "stream-1"}
    await leg.mark("b:2")
    await client.send_text(json.dumps({"event": "clearedAudio", "streamId": "stream-1"}))
    for name in ("b:1", "b:2"):
        await client.send_text(
            json.dumps({"event": "playedStream", "streamId": "stream-1", "name": name})
        )
    await settle()
    assert fired == ["b:2"]
    await finish(client, task)


async def test_dtmf():
    leg, client, task = await started()
    digits: list[str] = []
    leg.on_dtmf(digits.append)
    await client.send_text(
        json.dumps({"event": "dtmf", "dtmf": {"track": "inbound", "digit": "#", "timestamp": "1"}})
    )
    await settle()
    assert digits == ["#"]
    await finish(client, task)


async def test_disconnect_ends_frames():
    leg, client, task = await started()
    await client.close()
    await asyncio.wait_for(task, 1)
    assert [f async for f in leg.frames()] == []
