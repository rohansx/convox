import asyncio
import json

from dubhash.audio.frames import FRAME_BYTES
from dubhash.clock import FakeClock
from dubhash.legs.base import LegTransport
from dubhash.legs.webrtc import WebRtcLeg
from dubhash.legs.ws import MemoryWS, ws_pair


async def settle() -> None:
    for _ in range(25):
        await asyncio.sleep(0)


async def running(**kw) -> tuple[WebRtcLeg, MemoryWS, asyncio.Task, FakeClock]:
    clock = kw.pop("clock", FakeClock())
    server, client = ws_pair()
    leg = WebRtcLeg("a", server, clock=clock, **kw)
    task = asyncio.create_task(leg.run())
    await settle()
    return leg, client, task, clock


def test_satisfies_protocol():
    server, _ = ws_pair()
    assert isinstance(WebRtcLeg("a", server), LegTransport)


async def test_binary_inbound_becomes_frames_and_disconnect_ends_them():
    leg, client, task, _ = await running()
    await client.send_bytes(b"\x01" * 1000)
    await client.send_bytes(b"\x02" * 280)
    await client.close()
    await asyncio.wait_for(task, 1)
    frames = [f async for f in leg.frames()]
    assert [len(f) for f in frames] == [FRAME_BYTES, FRAME_BYTES]
    assert leg.closed


async def test_hello_message_is_recorded():
    leg, client, task, _ = await running()
    await client.send_text(json.dumps({"type": "hello", "side": "a", "lang": "hi"}))
    await settle()
    assert leg.hello == {"type": "hello", "side": "a", "lang": "hi"}
    await leg.close()
    await asyncio.wait_for(task, 1)


async def test_send_streams_binary_to_browser():
    leg, client, task, _ = await running()
    await leg.send(b"\x05" * 1280)
    assert client.drain() == [b"\x05" * 1280]
    await leg.close()
    await asyncio.wait_for(task, 1)


async def test_mark_emulated_from_playout_time():
    leg, client, task, clock = await running()
    fired: list[str] = []
    leg.on_mark(fired.append)
    await leg.send(b"\x00" * 6400)  # 200 ms
    await leg.mark("a:1")
    assert {"type": "mark", "name": "a:1"} in [json.loads(m) for m in client.drain()[1:]]
    clock.advance(199)
    leg.poll()
    assert fired == []
    clock.advance(1)
    leg.poll()
    assert fired == ["a:1"]
    await leg.close()
    await asyncio.wait_for(task, 1)


async def test_browser_acks_are_preferred_once_seen():
    leg, client, task, clock = await running(ack_grace_ms=10_000)
    fired: list[str] = []
    leg.on_mark(fired.append)
    await leg.send(b"\x00" * 32000)  # 1 s
    await leg.mark("m1")
    await client.send_text(json.dumps({"type": "mark", "name": "m1"}))  # drained early
    await settle()
    assert fired == ["m1"]
    clock.advance(1000)
    leg.poll()
    assert fired == ["m1"]  # no double fire

    await leg.send(b"\x00" * 3200)
    await leg.mark("m2")
    clock.advance(500)
    leg.poll()  # playout estimate says done, but acks win now
    await settle()
    assert fired == ["m1"]
    await client.send_text(json.dumps({"type": "mark", "name": "m2"}))
    await settle()
    assert fired == ["m1", "m2"]
    await leg.close()
    await asyncio.wait_for(task, 1)


async def test_missing_ack_falls_back_after_grace():
    leg, client, task, clock = await running(ack_grace_ms=0)
    fired: list[str] = []
    leg.on_mark(fired.append)
    await leg.mark("m0")
    await client.send_text(json.dumps({"type": "mark", "name": "m0"}))
    await settle()
    await leg.send(b"\x00" * 3200)
    await leg.mark("m1")
    clock.advance(100)
    leg.poll()
    await settle()
    assert fired == ["m0", "m1"]
    await leg.close()
    await asyncio.wait_for(task, 1)


async def test_clear_tells_browser_and_drops_pending_marks():
    leg, client, task, clock = await running()
    fired: list[str] = []
    leg.on_mark(fired.append)
    await leg.send(b"\x00" * 32000)
    await leg.mark("x")
    client.drain()
    await leg.clear()
    assert [json.loads(m) for m in client.drain()] == [{"type": "clear"}]
    await client.send_text(json.dumps({"type": "mark", "name": "x"}))
    clock.advance(2000)
    leg.poll()
    await settle()
    assert fired == []
    await leg.close()
    await asyncio.wait_for(task, 1)


async def test_dtmf_from_page_buttons():
    leg, client, task, _ = await running()
    digits: list[str] = []
    leg.on_dtmf(digits.append)
    await client.send_text(json.dumps({"type": "dtmf", "digit": "1"}))
    await client.send_text("garbage")
    await client.send_text(json.dumps({"type": "dtmf", "digit": "0"}))
    await settle()
    assert digits == ["1", "0"]
    await leg.close()
    await asyncio.wait_for(task, 1)


async def test_close_is_idempotent_and_closes_socket():
    leg, client, task, _ = await running()
    await leg.close()
    await leg.close()
    await asyncio.wait_for(task, 1)
    assert client.peer is not None and client.peer.closed
    assert [f async for f in leg.frames()] == []
    await leg.send(b"\x00" * 640)  # no-op after close


async def test_unknown_message_types_go_to_on_telemetry():
    leg, client, task, _ = await running()
    got: list[dict] = []
    leg.on_telemetry(got.append)
    msg = {"type": "telemetry", "kind": "play_start", "tag": "a:0", "client_ms": 1234.5}
    await client.send_text(json.dumps(msg))
    await client.send_text(json.dumps({"type": "dtmf", "digit": "1"}))
    await client.send_text("not json")
    await settle()
    assert got == [msg]
    await leg.close()
    await asyncio.wait_for(task, 1)


async def test_pings_carry_the_given_clock_until_close():
    leg, client, task, clock = await running()
    clock.advance(700)
    sent: list[int] = []
    leg.start_pings(clock.now_ms, interval_s=0.01, on_ping=sent.append)
    await asyncio.sleep(0.035)
    pings = [json.loads(m) for m in client.drain() if isinstance(m, str)]
    assert len(pings) >= 2
    assert pings[0] == {"type": "ping", "server_ms": 700} and sent[0] == 700
    await leg.close()
    await asyncio.wait_for(task, 1)
    await asyncio.sleep(0.03)
    assert not [m for m in client.drain() if isinstance(m, str) and "ping" in m]
