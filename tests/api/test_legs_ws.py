import base64
import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dubhash.api import calls
from dubhash.api.app import create_app
from dubhash.api.calls import CallBook, CallHandle, LegState
from dubhash.api.legs_ws import sniff_provider
from dubhash.config.pairs import load_pairs
from dubhash.config.settings import Settings
from dubhash.legs.exotel import ExotelLeg
from dubhash.legs.plivo import PlivoLeg
from tests.api.session_double import FakeSession, install

PCM = base64.b64encode(b"\x01\x00" * 1600).decode()  # 100 ms at 16 kHz

EXOTEL_START = {
    "event": "start",
    "sequence_number": 1,
    "stream_sid": "exo-stream-1",
    "start": {
        "stream_sid": "exo-stream-1",
        "call_sid": "exo-call-1",
        "from": "+919812345678",
        "to": "+918047123456",
        "custom_parameters": {"call": "c_test", "side": "a"},
        "media_format": {"encoding": "raw", "sample_rate": "16000", "bit_rate": "256"},
    },
}
PLIVO_START = {
    "event": "start",
    "sequenceNumber": 1,
    "start": {
        "callId": "plivo-call-1",
        "streamId": "plivo-stream-1",
        "accountId": "MA1",
        "tracks": ["inbound"],
        "mediaFormat": {"encoding": "audio/x-l16", "sampleRate": 16000},
    },
}


def wait_for(pred, within_s: float = 2.0) -> None:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.005)
    raise AssertionError("condition not met")


class Env:
    def __init__(self, provider: str | None = "exotel", **kw):
        s = Settings(_env_file=None, **{"dubhash_env": "test", **kw})
        self.app = create_app(s)
        self.registry = install(self.app)
        self.book = CallBook(no_answer_s=None, linger_s=0)
        self.app.dependency_overrides[calls.get_call_book] = lambda: self.book
        pair = load_pairs(s.pairs_file)["hi-kn"]
        self.session = FakeSession("c_test", pair, bus=self.app.state.bus)
        self.registry.create("c_test", self.session)
        if provider is not None:
            self.book.add(
                CallHandle(
                    call_id="c_test",
                    session=self.session,
                    provider=provider,
                    mode="interpret",
                    record=False,
                    legs={"a": LegState("hi-IN"), "b": LegState("kn-IN")},
                )
            )
        self.client = TestClient(self.app)


def close_code(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as ei:
        ws.receive_text()
    return ei.value.code


def test_sniff_provider():
    assert sniff_provider({"event": "connected"}) == "exotel"
    assert sniff_provider(EXOTEL_START) == "exotel"
    assert sniff_provider(PLIVO_START) == "plivo"
    assert sniff_provider({"event": "media"}) is None
    assert sniff_provider({"event": "start", "start": {}}) is None


def test_unknown_call_closes_4404():
    env = Env()
    with env.client.websocket_connect("/ws/leg?call=c_nope&side=a") as ws:
        assert close_code(ws) == 4404


def test_bad_side_closes_4400():
    env = Env()
    with env.client.websocket_connect("/ws/leg?call=c_test&side=z") as ws:
        assert close_code(ws) == 4400


def test_exotel_leg_attaches_and_runs_until_stop():
    env = Env()
    with env.client.websocket_connect("/ws/leg?call=c_test&side=a&sample-rate=16000") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(json.dumps(EXOTEL_START))
        ws.send_text(
            json.dumps({"event": "media", "media": {"chunk": 1, "timestamp": "0", "payload": PCM}})
        )
        wait_for(lambda: "a" in env.session.legs)
        leg = env.session.legs["a"]
        assert isinstance(leg, ExotelLeg)
        wait_for(lambda: leg.started.is_set())
        assert leg.sample_rate == 16000 and leg.stream_sid == "exo-stream-1"
        ws.send_text(json.dumps({"event": "stop", "stop": {"reason": "callended"}}))
        close_code(ws)
    state = env.book.get("c_test").legs["a"]
    wait_for(lambda: state.close_reason is not None)
    assert state.attached and state.close_reason == "callended"
    assert state.stream_sid == "exo-stream-1" and state.sample_rate == 16000
    assert leg.closed


def test_plivo_leg_attaches():
    env = Env(provider="plivo")
    with env.client.websocket_connect("/ws/leg?call=c_test&side=b") as ws:
        ws.send_text(json.dumps(PLIVO_START))
        wait_for(lambda: "b" in env.session.legs)
        leg = env.session.legs["b"]
        assert isinstance(leg, PlivoLeg)
        wait_for(lambda: leg.started.is_set())
        assert leg.stream_id == "plivo-stream-1" and leg.encoding == "l16"


def test_provider_mismatch_closes_4400():
    env = Env(provider="exotel")
    with env.client.websocket_connect("/ws/leg?call=c_test&side=b") as ws:
        ws.send_text(json.dumps(PLIVO_START))
        assert close_code(ws) == 4400
    assert env.session.legs == {}


def test_unrecognised_first_message_closes_4400():
    env = Env()
    with env.client.websocket_connect("/ws/leg?call=c_test&side=a") as ws:
        ws.send_text(json.dumps({"hello": 1}))
        assert close_code(ws) == 4400


def test_second_socket_for_same_side_closes_4409():
    env = Env()
    with env.client.websocket_connect("/ws/leg?call=c_test&side=a") as ws1:
        ws1.send_text(json.dumps({"event": "connected"}))
        wait_for(lambda: "a" in env.session.legs)
        with env.client.websocket_connect("/ws/leg?call=c_test&side=a") as ws2:
            ws2.send_text(json.dumps({"event": "connected"}))
            assert close_code(ws2) == 4409


def test_session_rejecting_attach_closes_4409():
    env = Env(provider=None)  # no CallBook entry: the session itself refuses
    env.session.legs["a"] = object()
    with env.client.websocket_connect("/ws/leg?call=c_test&side=a") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        assert close_code(ws) == 4409


def _basic(user: str, pw: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_exotel_basic_auth_enforced_when_configured():
    env = Env(exotel_ws_basic_user="KEY", exotel_ws_basic_pass="TOKEN")
    with env.client.websocket_connect("/ws/leg?call=c_test&side=a") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        assert close_code(ws) == 4401
    with env.client.websocket_connect(
        "/ws/leg?call=c_test&side=a", headers=_basic("KEY", "wrong")
    ) as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        assert close_code(ws) == 4401
    with env.client.websocket_connect(
        "/ws/leg?call=c_test&side=a", headers=_basic("KEY", "TOKEN")
    ) as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        wait_for(lambda: "a" in env.session.legs)


def test_exotel_ip_allowlist():
    env = Env(exotel_allowed_ips="10.0.0.9, testclient")
    with env.client.websocket_connect("/ws/leg?call=c_test&side=a") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        wait_for(lambda: "a" in env.session.legs)
    env = Env(exotel_allowed_ips="10.0.0.9")
    with env.client.websocket_connect("/ws/leg?call=c_test&side=a") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        assert close_code(ws) == 4401
