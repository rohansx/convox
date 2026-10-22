"""End-to-end through the real app: real CallSession, real WebRtcLeg, fake vendors.

A uvicorn server runs on a free port; two websocket clients play the two browser tabs.
A speaks, B must receive the earcon + translated audio; then B speaks and A receives.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
import time

import httpx
import numpy as np
import pytest
import uvicorn
import websockets

from convox.api.app import create_app
from convox.audio.earcon import EARCON
from convox.config.settings import Settings
from tests.audio_fixtures import frames, silence, speech


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def serve(**settings_kw):
    """The app on a free port in a uvicorn thread; yields "host:port"."""
    port = _free_port()
    settings = Settings(
        _env_file=None,
        convox_env="test",
        public_base_url=f"http://127.0.0.1:{port}",
        edge_wss_base=f"ws://127.0.0.1:{port}",
        **settings_kw,
    )
    app = create_app(settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    srv = uvicorn.Server(config)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    try:
        yield f"127.0.0.1:{port}"
    finally:
        srv.should_exit = True
        t.join(timeout=10)


@pytest.fixture
def server():
    with serve() as host:
        yield host


async def _speak(ws, pcm: bytes) -> None:
    for f in frames(pcm):
        await ws.send(f)
        await asyncio.sleep(0.02)


async def _collect(ws, stop: asyncio.Event, sink: bytearray) -> None:
    while not stop.is_set():
        try:
            msg = await asyncio.wait_for(ws.recv(), 0.1)
        except TimeoutError:
            continue
        except websockets.ConnectionClosed:
            return
        if isinstance(msg, bytes):
            sink += msg
        else:
            data = json.loads(msg)
            if data.get("type") == "mark":  # ack immediately, like a drained browser buffer
                await ws.send(msg)


def _contains(haystack: bytes, needle: bytes) -> bool:
    return needle[:640] in haystack


async def test_two_tab_loopback_call_interprets_both_ways(server):
    async with httpx.AsyncClient(base_url=f"http://{server}") as http:
        r = await http.post("/loopback/calls", json={"a_lang": "hi-IN", "b_lang": "kn-IN"})
        assert r.status_code == 201, r.text
        call_id = r.json()["call_id"]

    url = f"ws://{server}/ws/loopback?call={call_id}"
    heard = {"a": bytearray(), "b": bytearray()}
    stop = asyncio.Event()
    async with (
        websockets.connect(f"{url}&side=a") as wa,
        websockets.connect(f"{url}&side=b") as wb,
    ):
        readers = [
            asyncio.create_task(_collect(wa, stop, heard["a"])),
            asyncio.create_task(_collect(wb, stop, heard["b"])),
        ]
        await asyncio.sleep(1.0)  # opening line
        opening_b = len(heard["b"])

        await asyncio.gather(_speak(wa, speech(1200) + silence(1500)), _speak(wb, silence(2700)))
        await asyncio.sleep(1.0)
        assert len(heard["b"]) > opening_b, "B heard nothing after A spoke"
        assert _contains(bytes(heard["b"][opening_b:]), EARCON), "no earcon before translation"

        opening_a = len(heard["a"])
        await asyncio.gather(
            _speak(wb, speech(1200, f0=220) + silence(1500)), _speak(wa, silence(2700))
        )
        await asyncio.sleep(1.0)
        assert len(heard["a"]) > opening_a, "A heard nothing after B spoke"

        stop.set()
        await asyncio.gather(*readers)

    async with httpx.AsyncClient(base_url=f"http://{server}") as http:
        r = await http.get(f"/v1/calls/{call_id}/turns")
        assert r.status_code == 200, r.text
        turns = r.json()["turns"]
        assert [t["side"] for t in turns][:2] == ["a", "b"]
        audio = np.frombuffer(bytes(heard["b"]), dtype=np.int16)
        assert np.abs(audio).max() < 32767  # mixer never clips to the rail
