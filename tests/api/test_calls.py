import asyncio
import re

import httpx
import pytest

from dubhash.api import calls
from dubhash.api.app import create_app
from dubhash.api.calls import CallBook, new_call_id
from dubhash.config.settings import Settings
from dubhash.telephony import ProviderUnavailable
from dubhash.telephony.base import FakeDialer
from dubhash.types import Turn
from tests.api.session_double import install, until

BODY = {
    "a": {"number": "+919812345678", "lang": "hi-IN"},
    "b": {"number": "+919876543210", "lang": "kn-IN"},
    "provider": "exotel",
    "caller_id": "+918047123456",
}


def settings(**kw) -> Settings:
    return Settings(
        _env_file=None,
        **{"dubhash_env": "test", "edge_wss_base": "wss://edge.dubhash.in", **kw},
    )


class Env:
    def __init__(self, *, no_answer_s: float = 25.0, linger_s: float = 60.0, dialer=None, **kw):
        self.app = create_app(settings(**kw))
        self.registry = install(self.app)
        self.dialer = dialer or FakeDialer()
        self.book = CallBook(no_answer_s=no_answer_s, linger_s=linger_s)
        self.app.dependency_overrides[calls.get_call_book] = lambda: self.book
        self.app.dependency_overrides[calls.get_dialer_factory] = lambda: self._dialer_for

    def _dialer_for(self, provider: str):
        if provider == "plivo":
            raise ProviderUnavailable("plivo credentials not configured")
        return self.dialer

    @property
    def sessions(self):
        return self.app.state.fake_sessions

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t")


def test_call_id_format():
    ids = {new_call_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(re.fullmatch(r"c_[a-z2-7]{10}", i) for i in ids)


async def test_create_call_dials_both_legs_and_registers_session():
    env = Env()
    async with env.client() as c:
        r = await c.post("/v1/calls", json={**BODY, "mode": "bridge", "escape_dtmf": "9"})
    assert r.status_code == 201
    call_id = r.json()["call_id"]
    assert r.json() == {"call_id": call_id, "status": "ringing"}
    session = env.registry.get(call_id)
    assert session is env.sessions[0]
    assert not session.kw.get("co_located", False)  # two phone lines: no cross-talk
    assert session.pair.key == "hi-kn"
    assert session.mode == "bridge" and session.escape_dtmf == "9" and session.record is False
    dialed = {d.side: d for d in env.dialer.calls}
    assert dialed["a"].number == "+919812345678" and dialed["b"].number == "+919876543210"
    assert dialed["a"].caller_id == "+918047123456"
    assert dialed["a"].ws_url == (
        f"wss://edge.dubhash.in/ws/leg?call={call_id}&side=a&sample-rate=16000"
    )
    assert dialed["b"].ws_url.endswith(f"call={call_id}&side=b&sample-rate=16000")
    handle = env.book.get(call_id)
    assert handle.legs["a"].provider_call_sid == dialed["a"].sid
    assert env.book.find_sid(dialed["b"].sid) == (call_id, "b")
    # the console registry learns about the call too
    assert env.app.state.registry.get_call(call_id)["b_lang"] == "kn-IN"


async def test_unsupported_pair_is_422():
    env = Env()
    body = {**BODY, "b": {"number": "+919876543210", "lang": "od-IN"}}
    async with env.client() as c:
        r = await c.post("/v1/calls", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unsupported_pair"
    assert env.dialer.calls == [] and env.registry.list() == []


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({**BODY, "mode": "listen"}, "invalid_body"),
        ({**BODY, "a": {"number": "98123", "lang": "hi-IN"}}, "invalid_number"),
        ("not json", "invalid_body"),
    ],
)
async def test_bad_body_is_400(body, code):
    env = Env()
    async with env.client() as c:
        if isinstance(body, str):
            r = await c.post(
                "/v1/calls", content=body, headers={"content-type": "application/json"}
            )
        else:
            r = await c.post("/v1/calls", json=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == code


async def test_requires_api_key_outside_test_env():
    env = Env(dubhash_env="dev", platform_api_keys="swiggy:sk_live_1")
    async with env.client() as c:
        r = await c.post("/v1/calls", json=BODY)
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
        r = await c.post("/v1/calls", json=BODY, headers={"Authorization": "Bearer sk_live_1"})
        assert r.status_code == 201


async def test_provider_unavailable_is_503():
    env = Env()
    async with env.client() as c:
        r = await c.post("/v1/calls", json={**BODY, "provider": "plivo"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "provider_unavailable"
    assert env.registry.list() == []


async def test_dial_rejected_is_502_and_session_hung_up():
    env = Env(dialer=FakeDialer(fail_with="Invalid From number", fail_sides={"b"}))
    async with env.client() as c:
        r = await c.post("/v1/calls", json=BODY)
        assert r.status_code == 502
        err = r.json()["error"]
        assert err["code"] == "provider_error" and "Invalid From number" in err["message"]
        assert err["call_id"] is not None
        session = env.sessions[0]
        assert session.status == "ended" and session.hangups == ["provider_error"]
        r = await c.get(f"/v1/calls/{err['call_id']}")
        assert r.json()["status"] == "failed"


async def test_no_answer_hangs_up_after_timeout():
    env = Env(no_answer_s=0.05)
    async with env.client() as c:
        r = await c.post("/v1/calls", json=BODY)
        call_id = r.json()["call_id"]
        session = env.sessions[0]
        await until(lambda: session.status == "ended")
        assert session.hangups == ["no_answer"]
        r = await c.get(f"/v1/calls/{call_id}")
        assert r.json()["status"] == "failed"


async def test_no_answer_timer_stops_once_both_legs_attached():
    env = Env(no_answer_s=0.05)
    async with env.client() as c:
        r = await c.post("/v1/calls", json=BODY)
        call_id = r.json()["call_id"]
        for side in ("a", "b"):
            assert env.book.leg_attached(call_id, side)
        await asyncio.sleep(0.1)
        assert env.sessions[0].status == "ringing"  # the double only flips on attach_leg
        assert env.sessions[0].hangups == []


async def test_ended_session_is_removed_after_linger():
    env = Env(linger_s=0.01)
    async with env.client() as c:
        r = await c.post("/v1/calls", json=BODY)
        call_id = r.json()["call_id"]
        await env.sessions[0].hangup("hangup")
        await until(lambda: env.registry.get(call_id) is None)
        await until(lambda: env.book.get(call_id) is None)
        r = await c.get(f"/v1/calls/{call_id}")
        assert r.status_code == 404


async def test_get_call_summary():
    env = Env()
    async with env.client() as c:
        call_id = (await c.post("/v1/calls", json=BODY)).json()["call_id"]
        r = await c.get(f"/v1/calls/{call_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["call_id"] == call_id and body["status"] == "ringing"
        assert body["provider"] == "exotel" and body["mode"] == "interpret"
        assert body["legs"]["a"]["lang"] == "hi-IN"
        assert body["legs"]["b"]["provider_call_sid"].startswith("fake-")
        assert body["latency"] == {
            "turns": 0,
            "p50_first_audio_ms": None,
            "p95_first_audio_ms": None,
        }
        assert body["escape_used"] is False
        assert "9812345678" not in r.text  # numbers are not echoed back
        env.sessions[0].status = "active"
        assert (await c.get(f"/v1/calls/{call_id}")).json()["status"] == "in_progress"


async def test_get_unknown_call_is_404():
    env = Env()
    async with env.client() as c:
        for path in ("/v1/calls/c_nope", "/v1/calls/c_nope/turns"):
            r = await c.get(path)
            assert r.status_code == 404 and r.json()["error"]["code"] == "call_not_found"
        r = await c.post("/v1/calls/c_nope/hangup")
        assert r.status_code == 404


def _turn(call_id, seq, side="a", backchannel=False):
    return Turn(call_id=call_id, side=side, seq=seq, src_text="theek hai", tgt_text="ಸರಿ",
                t_speech_end=7000, t_play_start=8500, backchannel=backchannel)  # fmt: skip


async def test_turns_from_live_session_hide_text_without_record():
    env = Env()
    async with env.client() as c:
        call_id = (await c.post("/v1/calls", json=BODY)).json()["call_id"]
        env.sessions[0].turn_log.append(_turn(call_id, 1))
        r = await c.get(f"/v1/calls/{call_id}/turns")
    body = r.json()
    assert r.status_code == 200 and body["call_id"] == call_id
    assert body["text_retained"] is False
    (t,) = body["turns"]
    assert t["seq"] == 1 and t["src_text"] is None and t["tgt_text"] is None
    assert t["first_audio_ms"] == 1500


async def test_turns_with_record_keep_text():
    env = Env()
    async with env.client() as c:
        call_id = (await c.post("/v1/calls", json={**BODY, "record": True})).json()["call_id"]
        env.sessions[0].turn_log.extend([_turn(call_id, 2, "b"), _turn(call_id, 1)])
        body = (await c.get(f"/v1/calls/{call_id}/turns")).json()
    assert body["text_retained"] is True
    assert [t["seq"] for t in body["turns"]] == [1, 2]
    assert body["turns"][0]["src_text"] == "theek hai"


async def test_hangup_then_409():
    env = Env()
    async with env.client() as c:
        call_id = (await c.post("/v1/calls", json=BODY)).json()["call_id"]
        r = await c.post(f"/v1/calls/{call_id}/hangup")
        assert r.status_code == 200
        assert r.json() == {"call_id": call_id, "status": "completed"}
        assert env.sessions[0].hangups == ["api_hangup"]
        r = await c.post(f"/v1/calls/{call_id}/hangup")
        assert r.status_code == 409 and r.json()["error"]["code"] == "call_ended"
