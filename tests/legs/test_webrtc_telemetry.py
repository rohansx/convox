"""WebRtcLeg and the phone page's telemetry (L7): telemetry messages reach on_telemetry
callbacks (the call recorder), and ping() asks the page for a pong."""

from __future__ import annotations

import asyncio
import json

from tests.legs.test_webrtc import running, settle


async def test_telemetry_reaches_the_callbacks():
    leg, client, task, _ = await running()
    got: list[dict] = []
    leg.on_telemetry(got.append)
    msg = {"type": "telemetry", "kind": "play_start", "tag": "b:0", "client_ms": 1.0,
           "audio_ctx_ms": 2.0}  # fmt: skip
    await client.send_text(json.dumps(msg))
    await settle()
    assert got == [msg]
    await leg.close()
    await asyncio.wait_for(task, 1)


async def test_a_failing_callback_does_not_break_the_leg():
    leg, client, task, _ = await running()
    digits: list[str] = []

    def boom(msg: dict) -> None:
        raise RuntimeError("recorder bug")

    leg.on_telemetry(boom)
    leg.on_dtmf(digits.append)
    await client.send_text(json.dumps({"type": "telemetry", "kind": "mic_level", "rms": 0.1}))
    await client.send_text(json.dumps({"type": "dtmf", "digit": "1"}))
    await settle()
    assert not leg.closed and digits == ["1"]
    await leg.close()
    await asyncio.wait_for(task, 1)


async def test_ping_sends_the_server_time():
    leg, client, task, _ = await running()
    await leg.ping(1234)
    assert client.drain() == [json.dumps({"type": "ping", "server_ms": 1234})]
    await leg.close()
    await asyncio.wait_for(task, 1)
