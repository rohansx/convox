"""With CALL_RECORDING on, a loopback call through the real app leaves a recording."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import wave

import httpx
import pytest
import uvicorn
import websockets

from convox.api.app import create_app
from convox.config.settings import Settings
from tests.audio_fixtures import frames, silence, speech


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(**kw):
    port = _free_port()
    settings = Settings(
        _env_file=None,
        convox_env="test",
        public_base_url=f"http://127.0.0.1:{port}",
        edge_wss_base=f"ws://127.0.0.1:{port}",
        **kw,
    )
    app = create_app(settings)
    srv = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    return srv, t, f"127.0.0.1:{port}"


@pytest.fixture
def server(tmp_path):
    srv, t, host = _serve(call_recording=True, call_recording_dir=tmp_path)
    yield host, tmp_path
    srv.should_exit = True
    t.join(timeout=10)


async def _speak(ws, pcm: bytes) -> None:
    for f in frames(pcm):
        await ws.send(f)
        await asyncio.sleep(0.02)


async def _reader(ws, got: list) -> None:
    try:
        async for msg in ws:
            if isinstance(msg, str):
                data = json.loads(msg)
                got.append(data)
                if data.get("type") == "mark":
                    await ws.send(msg)
                elif data.get("type") == "ping":
                    pong = {"type": "telemetry", "kind": "pong", "server_ms": data["server_ms"]}
                    await ws.send(json.dumps({**pong, "client_ms": time.monotonic() * 1000}))
    except websockets.ConnectionClosed:
        pass


async def test_loopback_call_is_recorded_when_call_recording_is_on(server):
    host, base = server
    async with httpx.AsyncClient(base_url=f"http://{host}") as http:
        r = await http.post("/loopback/calls", json={"a_lang": "hi-IN", "b_lang": "kn-IN"})
        call_id = r.json()["call_id"]
    url = f"ws://{host}/ws/loopback?call={call_id}"
    got: dict[str, list] = {"a": [], "b": []}
    async with (
        websockets.connect(f"{url}&side=a") as wa,
        websockets.connect(f"{url}&side=b") as wb,
    ):
        readers = [asyncio.create_task(_reader(ws, got[s])) for s, ws in (("a", wa), ("b", wb))]
        await asyncio.sleep(0.5)
        await asyncio.gather(_speak(wa, speech(1200) + silence(1500)), _speak(wb, silence(2700)))
        await asyncio.sleep(1.0)
        await wa.send(json.dumps({"type": "telemetry", "kind": "play_start", "client_ms": 1.0}))
        await asyncio.sleep(0.1)
    await asyncio.gather(*readers)

    dirs = list(base.glob(f"*/{call_id}"))
    assert len(dirs) == 1
    d = dirs[0]
    deadline = time.monotonic() + 5
    while not (d / "summary.json").exists():
        assert time.monotonic() < deadline, "no summary.json"
        await asyncio.sleep(0.05)
    assert any(m.get("type") == "ping" for m in got["a"]), "no server ping"
    events = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]
    kinds = {e["kind"] for e in events}
    assert {"rec.meta", "leg", "call", "turn", "leg.mark", "leg.mark_ack"} <= kinds
    meta = events[0]["payload"]
    assert meta["pair"] and meta["sides"]["a"]["lang"] == "hi-IN"
    client = [json.loads(x) for x in (d / "client.jsonl").read_text().splitlines()]
    assert {"pong", "play_start"} <= {c["kind"] for c in client}
    sizes = set()
    for name in ("a_in", "b_in", "a_out", "b_out"):
        with wave.open(str(d / f"{name}.wav"), "rb") as w:
            sizes.add(w.getnframes())
    assert len(sizes) == 1 and sizes.pop() > 16000
    summary = json.loads((d / "summary.json").read_text())
    assert summary["call_id"] == call_id and summary["turns"] >= 1


async def test_nothing_is_recorded_when_call_recording_is_off(tmp_path):
    srv, t, host = _serve(call_recording=False, call_recording_dir=tmp_path)
    try:
        async with httpx.AsyncClient(base_url=f"http://{host}") as http:
            r = await http.post("/loopback/calls", json={"a_lang": "hi-IN", "b_lang": "kn-IN"})
            call_id = r.json()["call_id"]
        url = f"ws://{host}/ws/loopback?call={call_id}"
        got: list = []
        async with (
            websockets.connect(f"{url}&side=a") as wa,
            websockets.connect(f"{url}&side=b") as wb,
        ):
            readers = [asyncio.create_task(_reader(ws, got)) for ws in (wa, wb)]
            await asyncio.sleep(0.3)
        await asyncio.gather(*readers)
        await asyncio.sleep(0.3)
        # pings still flow (phone telemetry goes to the server log, L7); only disk writes stop
        assert not list(tmp_path.iterdir())
    finally:
        srv.should_exit = True
        t.join(timeout=10)
