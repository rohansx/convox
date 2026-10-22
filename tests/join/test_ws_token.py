"""/ws/loopback takes a join token (?t=) as an alternative to the console's auth."""

import json

import pytest
from starlette.websockets import WebSocketDisconnect

from convox.join.tokens import make_join_token
from convox.legs.webrtc import WebRtcLeg
from tests.api.test_loopback import Env, _close_clients, wait_for  # noqa: F401


def _call(env: Env) -> str:
    r = env.client.post("/loopback/calls", json={"a_lang": "mr-IN", "b_lang": "hi-IN"})
    assert r.status_code == 201, r.text
    return r.json()["call_id"]


def test_valid_token_attaches_the_leg():
    env = Env()
    call_id = _call(env)
    tok = make_join_token(env.app.state.settings, call_id, "a")
    with env.client.websocket_connect(f"/ws/loopback?call={call_id}&side=a&t={tok}") as ws:
        ws.send_text(json.dumps({"type": "hello", "side": "a", "lang": "mr-IN"}))
        wait_for(lambda: "a" in env.sessions[0].legs)
        assert isinstance(env.sessions[0].legs["a"], WebRtcLeg)


@pytest.mark.parametrize("which", ["garbage", "other_side", "other_call"])
def test_bad_token_is_rejected(which):
    env = Env()
    call_id = _call(env)
    s = env.app.state.settings
    tok = {
        "garbage": "123.abc",
        "other_side": make_join_token(s, call_id, "b"),
        "other_call": make_join_token(s, "c_other", "a"),
    }[which]
    with env.client.websocket_connect(f"/ws/loopback?call={call_id}&side=a&t={tok}") as ws:
        with pytest.raises(WebSocketDisconnect) as ei:
            ws.receive_text()
        assert ei.value.code == 4401
    assert "a" not in env.sessions[0].legs
