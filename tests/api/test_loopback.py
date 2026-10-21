import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dubhash.api import calls
from dubhash.api.app import create_app
from dubhash.api.calls import CallBook
from dubhash.config.settings import Settings
from dubhash.legs.webrtc import WebRtcLeg
from tests.api.session_double import install


def wait_for(pred, within_s: float = 2.0) -> None:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.005)
    raise AssertionError("condition not met")


_open: list[TestClient] = []


@pytest.fixture(autouse=True)
def _close_clients():
    yield
    while _open:
        _open.pop().__exit__(None, None, None)


class Env:
    def __init__(self, **kw):
        s = Settings(
            _env_file=None,
            **{
                "dubhash_env": "test",
                "public_base_url": "https://dev.dubhash.in",
                "edge_wss_base": "wss://dev.dubhash.in",
                **kw,
            },
        )
        self.app = create_app(s)
        self.registry = install(self.app)
        self.book = CallBook(no_answer_s=0.01, linger_s=60)
        self.app.dependency_overrides[calls.get_call_book] = lambda: self.book
        # one portal for the whole test, so background supervisors keep running
        self._ctx = TestClient(self.app)
        self.client = self._ctx.__enter__()
        _open.append(self._ctx)

    @property
    def sessions(self):
        return self.app.state.fake_sessions


def test_page_is_served():
    r = Env().client.get("/loopback")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "qs.get('ws')" in r.text


def test_create_loopback_call_returns_page_urls():
    env = Env()
    r = env.client.post(
        "/loopback/calls", json={"a_lang": "hi-IN", "b_lang": "kn-IN", "mode": "bridge"}
    )
    assert r.status_code == 201
    body = r.json()
    call_id = body["call_id"]
    session = env.registry.get(call_id)
    assert session is env.sessions[0] and session.mode == "bridge"
    for side, lang in (("a", "hi-IN"), ("b", "kn-IN")):
        url = urlparse(body[f"{side}_url"])
        assert f"{url.scheme}://{url.netloc}{url.path}" == "https://dev.dubhash.in/loopback"
        q = parse_qs(url.query)
        assert q["side"] == [side] and q["lang"] == [lang]
        assert q["ws"] == [f"wss://dev.dubhash.in/ws/loopback?call={call_id}&side={side}"]
    time.sleep(0.05)  # longer than no_answer_s: loopback calls never time out
    assert session.status == "ringing" and session.hangups == []


def test_loopback_calls_are_co_located():
    """Both phones are in one room: cross-talk gate on, no ducked original."""
    env = Env()
    r = env.client.post("/loopback/calls", json={"a_lang": "hi-IN", "b_lang": "kn-IN"})
    assert r.status_code == 201
    assert env.sessions[0].kw.get("co_located") is True


def test_loopback_call_can_keep_text():
    """record=true keeps src_text/tgt_text in GET /v1/calls/{id}/turns (live tools read it)."""
    env = Env()
    r = env.client.post(
        "/loopback/calls", json={"a_lang": "hi-IN", "b_lang": "kn-IN", "record": True}
    )
    assert r.status_code == 201
    call_id = r.json()["call_id"]
    assert env.sessions[0].record is True
    assert env.book.get(call_id).record is True
    env.client.post("/loopback/calls", json={"a_lang": "hi-IN", "b_lang": "kn-IN"})
    assert env.sessions[1].record is False


def test_loopback_call_picks_a_named_pair():
    env = Env()
    r = env.client.post(
        "/loopback/calls", json={"a_lang": "hi-IN", "b_lang": "kn-IN", "pair": "hi-kn-direct"}
    )
    assert r.status_code == 201, r.text
    assert env.sessions[0].pair.key == "hi-kn-direct"
    env.client.post("/loopback/calls", json={"a_lang": "hi-IN", "b_lang": "kn-IN"})
    assert env.sessions[1].pair.key == "hi-kn"


def test_loopback_named_pair_in_the_other_orientation_is_swapped():
    env = Env()
    r = env.client.post(
        "/loopback/calls", json={"a_lang": "kn-IN", "b_lang": "hi-IN", "pair": "hi-kn-direct"}
    )
    assert r.status_code == 201, r.text
    pair = env.sessions[0].pair
    assert pair.key == "hi-kn-direct" and (pair.a.lang, pair.b.lang) == ("kn-IN", "hi-IN")
    env.client.post("/loopback/calls", json={"a_lang": "kn-IN", "b_lang": "hi-IN"})
    pair = env.sessions[1].pair
    assert pair.key == "hi-kn" and pair.b.stt == "sarvam-translate"  # the Hindi side's pivot


@pytest.mark.parametrize(
    "body",
    [
        {"a_lang": "hi-IN", "b_lang": "kn-IN", "pair": "nope"},
        {"a_lang": "hi-IN", "b_lang": "ta-IN", "pair": "hi-kn-direct"},  # langs disagree
    ],
)
def test_loopback_named_pair_must_exist_and_match(body):
    r = Env().client.post("/loopback/calls", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_pair"


def test_unsupported_loopback_pair():
    r = Env().client.post("/loopback/calls", json={"a_lang": "hi-IN", "b_lang": "od-IN"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_pair"


def test_new_form_and_submit():
    env = Env()
    r = env.client.get("/loopback/new")
    assert r.status_code == 200 and "<form" in r.text
    assert 'value="hi-IN"' in r.text and 'value="kn-IN"' in r.text
    r = env.client.post(
        "/loopback/new",
        content="a_lang=hi-IN&b_lang=kn-IN&mode=interpret",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200
    call_id = env.sessions[0].call_id
    assert call_id in r.text
    assert "https://dev.dubhash.in/loopback?ws=" in r.text
    r = env.client.post(
        "/loopback/new",
        content="a_lang=hi-IN&b_lang=od-IN",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 422 and "no config for pair" in r.text


def test_ws_loopback_attaches_webrtc_leg():
    env = Env()
    call_id = env.client.post(
        "/loopback/calls", json={"a_lang": "hi-IN", "b_lang": "kn-IN"}
    ).json()["call_id"]
    with env.client.websocket_connect(f"/ws/loopback?call={call_id}&side=b") as ws:
        ws.send_text(json.dumps({"type": "hello", "side": "b", "lang": "kn-IN"}))
        wait_for(lambda: "b" in env.sessions[0].legs)
        leg = env.sessions[0].legs["b"]
        assert isinstance(leg, WebRtcLeg)
        wait_for(lambda: leg.hello.get("lang") == "kn-IN")
        ws.send_bytes(b"\x00\x00" * 320)
    wait_for(lambda: env.book.get(call_id).legs["b"].close_reason is not None)


def test_ws_loopback_unknown_call():
    env = Env()
    with env.client.websocket_connect("/ws/loopback?call=c_nope&side=a") as ws:
        with pytest.raises(WebSocketDisconnect) as ei:
            ws.receive_text()
        assert ei.value.code == 4404


def test_disabled_when_webrtc_leg_off():
    env = Env(enable_webrtc_leg=False)
    assert env.client.get("/loopback").status_code == 404
    assert (
        env.client.post("/loopback/calls", json={"a_lang": "hi-IN", "b_lang": "kn-IN"}).status_code
        == 404
    )
