import json
import re

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from convox.api.app import create_app
from convox.config.settings import Settings
from convox.providers.fakes import FakeMT, FakeSTT, FakeTTS
from convox.ward import routes
from convox.ward.service import WardProviders
from tests.audio_fixtures import harmonic

PRESS = harmonic(1000, f0=120.0)


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **{"convox_env": "test", **kw})


class Factory:
    def __init__(self, script=None, dictionary=None):
        self.script = script
        self.dictionary = dictionary
        self.pairs = []

    def __call__(self, pair):
        self.pairs.append(pair)
        return WardProviders(
            stt={"a": FakeSTT(script=self.script), "b": FakeSTT(script=self.script)},
            mt=FakeMT(dictionary=self.dictionary),
            tts={"a": FakeTTS(), "b": FakeTTS()},
        )


@pytest.fixture
def factory():
    return Factory(script=["2 tablets at 8 pm"], dictionary={"2 tablets at 8 pm": "2 गोळ्या रात्री"})


@pytest.fixture
def client(factory):
    app = create_app(settings())
    app.dependency_overrides[routes.ward_providers_factory] = lambda: factory
    with TestClient(app) as c:
        yield c


def press(ws, side="a", pcm=PRESS):
    ws.send_json({"type": "start", "side": side})
    for i in range(0, len(pcm), 640):
        ws.send_bytes(pcm[i : i + 640])
    ws.send_json({"type": "end"})


def receive_turn(ws):
    turn = ws.receive_json()
    audio = bytearray()
    while True:
        msg = ws.receive()
        if msg.get("bytes") is not None:
            audio += msg["bytes"]
            continue

        end = json.loads(msg["text"])
        return turn, bytes(audio), end


# --- page ----------------------------------------------------------------------------


def test_ward_page_renders_and_references_its_assets(client):
    r = client.get("/ward")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    body = r.text
    assert "/static/console/ward.js" in body and "/static/console/ward.css" in body
    assert 'value="mr-IN" selected' in body and 'value="hi-IN" selected' in body
    assert "Marathi" in body and "Hindi" in body
    assert re.search(r'data-ws-token="[^"]+"', body)


def test_ward_page_offers_marathi_preset_buttons(client):
    body = client.get("/ward").text
    assert 'data-preset-a="mr-IN" data-preset-b="hi-IN"' in body
    assert 'data-preset-a="mr-IN" data-preset-b="en-IN"' in body
    assert 'data-preset-a="gu-IN" data-preset-b="hi-IN"' in body
    assert 'data-preset-a="gu-IN" data-preset-b="en-IN"' in body
    js = client.get("/static/console/ward.js").text
    assert "data-preset-a" in js or "presetA" in js


def test_old_ward_stub_is_gone():
    from convox.api import console

    assert "/ward" not in [getattr(r, "path", None) for r in console.router.routes]
    assert "/ward" in [getattr(r, "path", None) for r in routes.router.routes]


def test_ward_assets_are_served(client):
    for name in ("ward.js", "ward.css"):
        r = client.get(f"/static/console/{name}")
        assert r.status_code == 200, name
    js = client.get("/static/console/ward.js").text
    assert "audioWorklet" in js and "/ws/ward" in js


# --- websocket -----------------------------------------------------------------------


def test_scripted_press_returns_turn_audio_and_audio_end(client, factory):
    with client.websocket_connect("/ws/ward") as ws:
        ws.send_json({"type": "config", "a": "mr-IN", "b": "hi-IN"})
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["a"] == "mr-IN" and ready["b"] == "hi-IN"
        assert ready["session"].startswith("w_")
        press(ws, "a")
        turn, audio, end = receive_turn(ws)
    assert turn["type"] == "turn" and turn["side"] == "a"
    assert turn["src_text"] == "2 tablets at 8 pm" and turn["tgt_text"] == "2 गोळ्या रात्री"
    assert turn["warning"] == "numbers differ: 8 missing in translation"
    assert {"stt_ms", "mt_ms"} <= set(turn["timings"])
    assert len(audio) > 0 and len(audio) % 2 == 0
    assert end["type"] == "audio_end" and "tts_ttfb_ms" in end["timings"]
    assert factory.pairs[0].a.lang == "mr-IN" and factory.pairs[0].b.lang == "hi-IN"


def test_export_returns_transcript(client):
    with client.websocket_connect("/ws/ward") as ws:
        ws.send_json({"type": "config", "a": "mr-IN", "b": "hi-IN"})
        ws.receive_json()
        press(ws, "a")
        receive_turn(ws)
        ws.send_json({"type": "export"})
        msg = ws.receive_json()
    assert msg["type"] == "transcript"
    assert msg["json"]["pair"] == {"a": "mr-IN", "b": "hi-IN"}
    assert msg["json"]["turns"][0]["src_text"] == "2 tablets at 8 pm"
    assert "2 गोळ्या रात्री" in msg["text"]


def test_empty_press_and_cancel(client):
    with client.websocket_connect("/ws/ward") as ws:
        ws.send_json({"type": "config", "a": "mr-IN", "b": "hi-IN"})
        ws.receive_json()
        press(ws, "b", pcm=b"\x00\x00" * 160)  # 10 ms: nothing heard
        assert ws.receive_json() == {"type": "empty", "side": "b"}
        ws.send_json({"type": "start", "side": "a"})
        ws.send_bytes(PRESS[:640])
        ws.send_json({"type": "cancel"})
        ws.send_json({"type": "export"})
        msg = ws.receive_json()
    assert msg["type"] == "transcript" and msg["json"]["turns"] == []


def test_reset_starts_a_new_transcript(client):
    with client.websocket_connect("/ws/ward") as ws:
        ws.send_json({"type": "config", "a": "mr-IN", "b": "hi-IN"})
        first = ws.receive_json()["session"]
        press(ws, "a")
        receive_turn(ws)
        ws.send_json({"type": "reset"})
        ready = ws.receive_json()
        ws.send_json({"type": "export"})
        msg = ws.receive_json()
    assert ready["type"] == "ready" and ready["session"] != first
    assert msg["json"]["turns"] == [] and msg["json"]["pair"] == {"a": "mr-IN", "b": "hi-IN"}


def test_reconfigure_switches_languages(client, factory):
    with client.websocket_connect("/ws/ward") as ws:
        ws.send_json({"type": "config", "a": "mr-IN", "b": "hi-IN"})
        ws.receive_json()
        ws.send_json({"type": "config", "a": "kn-IN", "b": "hi-IN"})
        ready = ws.receive_json()
    assert ready["a"] == "kn-IN" and factory.pairs[-1].a.lang == "kn-IN"


@pytest.mark.parametrize(
    "first",
    [
        {"type": "start", "side": "a"},  # before config
        {"type": "config", "a": "hi-IN", "b": "hi-IN"},
        {"type": "config", "a": "xx", "b": "hi-IN"},
    ],
)
def test_protocol_errors_are_reported_not_fatal(client, first):
    with client.websocket_connect("/ws/ward") as ws:
        ws.send_json(first)
        err = ws.receive_json()
        assert err["type"] == "error" and err["message"]
        ws.send_json({"type": "config", "a": "mr-IN", "b": "hi-IN"})
        assert ws.receive_json()["type"] == "ready"


def test_bad_side_is_an_error(client):
    with client.websocket_connect("/ws/ward") as ws:
        ws.send_json({"type": "config", "a": "mr-IN", "b": "hi-IN"})
        ws.receive_json()
        ws.send_json({"type": "start", "side": "c"})
        assert ws.receive_json()["type"] == "error"


# --- auth ----------------------------------------------------------------------------


def test_ws_token_roundtrip():
    s = settings(console_password="pw")
    tok = routes.make_ws_token(s, now=1000)
    assert routes.verify_ws_token(s, tok, now=1001)
    assert not routes.verify_ws_token(s, tok, now=1000 + routes.TOKEN_TTL_S + 1)
    assert not routes.verify_ws_token(settings(console_password="other"), tok, now=1001)
    assert not routes.verify_ws_token(s, "garbage", now=1001)
    assert not routes.verify_ws_token(s, "", now=1001)


def test_ws_rejects_a_bad_token(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/ward?token=1.bad") as ws:
            ws.receive_json()
    assert exc.value.code == 1008


def test_ws_accepts_the_page_token(client):
    token = re.search(r'data-ws-token="([^"]+)"', client.get("/ward").text).group(1)
    with client.websocket_connect(f"/ws/ward?token={token}") as ws:
        ws.send_json({"type": "config", "a": "mr-IN", "b": "hi-IN"})
        assert ws.receive_json()["type"] == "ready"
