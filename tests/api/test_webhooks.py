import asyncio
import hashlib
import hmac
import json
import xml.etree.ElementTree as ET

import httpx

from convox.api import calls
from convox.api.app import create_app
from convox.api.calls import CallBook, CallHandle, LegState
from convox.api.webhooks import PlatformNotifier, sign
from convox.config.pairs import load_pairs
from convox.config.settings import Settings
from convox.types import CallEvent, Turn
from tests.api.session_double import FakeSession, install, until


class Env:
    def __init__(self, provider="exotel", **kw):
        s = Settings(
            _env_file=None,
            **{"convox_env": "test", "edge_wss_base": "wss://edge.x", **kw},
        )
        self.app = create_app(s)
        self.bus = self.app.state.bus
        self.registry = install(self.app)
        self.book = CallBook(no_answer_s=None, linger_s=0)
        self.app.dependency_overrides[calls.get_call_book] = lambda: self.book
        self.pair = load_pairs(s.pairs_file)["hi-kn"]
        self.session = FakeSession("c_w", self.pair, bus=self.bus)
        self.registry.create("c_w", self.session)
        self.handle = CallHandle(
            call_id="c_w",
            session=self.session,
            provider=provider,
            mode="interpret",
            record=False,
            legs={"a": LegState("hi-IN"), "b": LegState("kn-IN")},
        )
        self.book.add(self.handle)
        self.book.record_sid("c_w", "a", "sid-a")
        self.book.record_sid("c_w", "b", "sid-b")

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t")


async def collect(bus, n_ticks=3):
    events = []

    async def reader():
        async for ev in bus.subscribe():
            events.append(ev)

    task = asyncio.create_task(reader())
    await asyncio.sleep(0)
    return events, task


async def test_exotel_answered_publishes_leg_event_without_hangup():
    env = Env()
    events, task = await collect(env.bus)
    async with env.client() as c:
        r = await c.post(
            "/webhooks/exotel?call=c_w&side=a",
            data={"CallSid": "sid-a", "Status": "in-progress", "EventType": "answered"},
        )
    assert r.status_code == 200
    await until(lambda: events)
    task.cancel()
    ev = events[0]
    assert ev.call_id == "c_w" and ev.kind == "leg"
    assert ev.payload == {"side": "a", "status": "answered", "provider": "exotel"}
    assert env.session.hangups == []
    assert env.handle.legs["a"].status == "answered"


async def test_exotel_busy_by_sid_hangs_up_and_call_fails():
    env = Env()
    async with env.client() as c:
        r = await c.post(
            "/webhooks/exotel",
            data={"CallSid": "sid-b", "Status": "busy", "EventType": "terminal"},
        )
        assert r.status_code == 200
        await until(lambda: env.session.status == "ended")
        assert env.session.hangups == ["busy"]
        assert (await c.get("/v1/calls/c_w")).json()["status"] == "failed"
        assert env.handle.legs["b"].close_reason == "busy"


async def test_duplicate_callbacks_are_ignored():
    env = Env()
    events, task = await collect(env.bus)
    form = {"CallSid": "sid-a", "Status": "in-progress", "EventType": "answered"}
    async with env.client() as c:
        await c.post("/webhooks/exotel", data=form)
        await c.post("/webhooks/exotel", data=form)
    await asyncio.sleep(0.02)
    task.cancel()
    assert len([e for e in events if e.kind == "leg"]) == 1


async def test_unknown_call_is_200():
    env = Env()
    async with env.client() as c:
        r = await c.post("/webhooks/exotel", data={"CallSid": "who", "Status": "failed"})
        assert r.status_code == 200
        r = await c.post("/webhooks/plivo?call=c_nope&side=a", data={"CallStatus": "busy"})
        assert r.status_code == 200
    assert env.session.hangups == []


async def test_plivo_no_answer_hangs_up():
    env = Env(provider="plivo")
    async with env.client() as c:
        r = await c.post(
            "/webhooks/plivo?call=c_w&side=b",
            data={"CallUUID": "uuid-b", "CallStatus": "no-answer", "HangupCause": "NO_ANSWER"},
        )
    assert r.status_code == 200
    await until(lambda: env.session.status == "ended")
    assert env.session.hangups == ["no_answer"]


async def test_completed_ends_a_live_session():
    env = Env(provider="plivo")
    env.session.status = "active"
    async with env.client() as c:
        await c.post("/webhooks/plivo?call=c_w&side=a", data={"CallStatus": "completed"})
    await until(lambda: env.session.status == "ended")
    assert env.session.hangups == ["completed"]
    assert env.handle.api_status() == "completed"


async def test_plivo_answer_returns_stream_xml():
    env = Env(provider="plivo", plivo_stream_content_type="audio/x-mulaw;rate=8000")
    async with env.client() as c:
        for method in ("GET", "POST"):
            r = await c.request(method, "/telephony/plivo/answer?call=c_w&side=b")
            assert r.status_code == 200 and "xml" in r.headers["content-type"]
            stream = ET.fromstring(r.text).find("Stream")
            assert stream.text == "wss://edge.x/ws/leg?call=c_w&side=b&sample-rate=16000"
            assert stream.get("contentType") == "audio/x-mulaw;rate=8000"
        r = await c.post("/telephony/plivo/answer?call=c_nope&side=b")
        assert ET.fromstring(r.text).find("Hangup") is not None


async def test_exotel_stream_url_resolves_custom_field_or_sid():
    env = Env()
    async with env.client() as c:
        r = await c.get("/telephony/exotel/stream", params={"CustomField": "c_w:b"})
        assert r.json() == {"url": "wss://edge.x/ws/leg?call=c_w&side=b&sample-rate=16000"}
        r = await c.post("/telephony/exotel/stream", data={"CallSid": "sid-a"})
        assert r.json()["url"].endswith("call=c_w&side=a&sample-rate=16000")
        r = await c.get("/telephony/exotel/stream", params={"CallSid": "nope"})
        assert r.status_code == 404


# --- PlatformNotifier -----------------------------------------------------------------


def verify(secret: str, req: httpx.Request) -> dict:
    expected = "sha256=" + hmac.new(secret.encode(), req.content, hashlib.sha256).hexdigest()
    assert req.headers["x-convox-signature"] == expected
    assert req.headers["content-type"] == "application/json"
    return json.loads(req.content)


def test_sign():
    assert sign("k", b"{}") == "sha256=" + hmac.new(b"k", b"{}", hashlib.sha256).hexdigest()


def _notifier(env, handler, *, record=False, retries=3):
    env.handle.record = record
    env.handle.webhook = "https://platform.example/hooks"
    return PlatformNotifier(
        env.handle,
        env.bus,
        secret="test-webhook-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retries=retries,
        backoff_s=0,
    )


async def test_notifier_sends_started_turn_and_ended():
    env = Env()
    got: list[dict] = []

    def handler(req):
        got.append(verify("test-webhook-key", req))
        assert str(req.url) == "https://platform.example/hooks"
        return httpx.Response(200)

    task = asyncio.create_task(_notifier(env, handler).run())
    await asyncio.sleep(0)
    await env.session.attach_leg("a", _Leg())
    await env.session.attach_leg("b", _Leg())
    turn = Turn(call_id="c_w", side="a", seq=1, src_text="theek hai", tgt_text="ಸರಿ",
                t_speech_end=1000, t_play_start=2300, clipped_ms=400)  # fmt: skip
    env.session.add_turn(turn)
    env.session.add_turn(Turn(call_id="c_w", side="b", seq=2, backchannel=True))
    env.handle.end("hangup")
    await env.session.hangup("hangup")
    await asyncio.wait_for(task, 1)
    assert [g["event"] for g in got] == ["call.started", "turn", "call.ended"]
    assert all(g["call_id"] == "c_w" and g["ts"].endswith("Z") for g in got)
    assert got[0]["data"] == {
        "mode": "interpret",
        "a": {"lang": "hi-IN", "lang_source": "profile"},
        "b": {"lang": "kn-IN", "lang_source": "profile"},
    }
    assert got[1]["data"] == {
        "seq": 1,
        "side": "a",
        "src_text": "theek hai",
        "tgt_text": "ಸರಿ",
        "first_audio_ms": 1300,
        "clipped": True,
        "clipped_ms": 400,
    }
    ended = got[2]["data"]
    assert ended["status"] == "completed" and ended["turns"] == 1
    assert ended["p50_first_audio_ms"] == 1500 and ended["escape_used"] is False
    assert set(ended["legs"]) == {"a", "b"}
    assert ended["transcript"] is None


async def test_notifier_transcript_when_recording():
    env = Env()
    got: list[dict] = []
    task = asyncio.create_task(
        _notifier(env, lambda r: got.append(json.loads(r.content)) or httpx.Response(200),
                  record=True).run()
    )  # fmt: skip
    await asyncio.sleep(0)
    env.session.add_turn(Turn(call_id="c_w", side="a", seq=1, src_text="x", tgt_text="y"))
    await env.session.hangup("hangup")
    await asyncio.wait_for(task, 1)
    assert got[-1]["data"]["transcript"] == [
        {"seq": 1, "side": "a", "src_text": "x", "tgt_text": "y"}
    ]


async def test_notifier_sends_ended_even_without_a_call_event():
    env = Env()
    got: list[str] = []
    n = _notifier(env, lambda r: got.append(json.loads(r.content)["event"]) or httpx.Response(200))
    n.grace_s = 0.01
    task = asyncio.create_task(n.run())
    await asyncio.sleep(0)
    env.session.status = "ended"
    env.session._closed.set()  # closed without publishing
    await asyncio.wait_for(task, 1)
    assert got == ["call.ended"]


async def test_notifier_retries_5xx_then_gives_up():
    env = Env()
    attempts: list[int] = []

    def handler(req):
        attempts.append(1)
        return httpx.Response(503)

    n = _notifier(env, handler, retries=2)
    assert await n.deliver("turn", {"seq": 1}) is False
    assert len(attempts) == 3

    attempts.clear()
    codes = iter([500, 200])
    n = _notifier(env, lambda r: attempts.append(1) or httpx.Response(next(codes)))
    assert await n.deliver("turn", {"seq": 1}) is True and len(attempts) == 2

    attempts.clear()
    n = _notifier(env, lambda r: attempts.append(1) or httpx.Response(400))
    assert await n.deliver("turn", {"seq": 1}) is False and len(attempts) == 1


async def test_notifier_retries_network_errors():
    env = Env()
    calls_ = []

    def handler(req):
        calls_.append(1)
        if len(calls_) == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(204)

    assert await _notifier(env, handler).deliver("turn", {}) is True


async def test_create_call_with_webhook_starts_notifier():
    from convox.telephony.base import FakeDialer

    env = Env()
    env.app.dependency_overrides[calls.get_dialer_factory] = lambda: lambda p: FakeDialer()
    got: list[str] = []
    env.app.state.webhook_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: got.append(json.loads(r.content)["event"]) or httpx.Response(200)
        )
    )
    body = {
        "a": {"number": "+919812345678", "lang": "hi-IN"},
        "b": {"number": "+919876543210", "lang": "kn-IN"},
        "caller_id": "+918047123456",
        "webhook": "https://platform.example/hooks",
    }
    async with env.client() as c:
        call_id = (await c.post("/v1/calls", json=body)).json()["call_id"]
        await asyncio.sleep(0.01)
        env.bus.publish(CallEvent(call_id, 0, "call", {"status": "active"}))
        await until(lambda: got == ["call.started"])


class _Leg:
    async def close(self):
        pass


async def test_exotel_stream_url_logs_request_shape_with_numbers_masked(caplog):
    env = Env()
    caplog.set_level("INFO", logger="convox.api.webhooks")
    async with env.client() as c:
        await c.post(
            "/telephony/exotel/stream",
            data={"CallSid": "sid-a", "From": "09172522532", "CustomField": "c_w:a"},
        )
    rec = next(r for r in caplog.records if r.getMessage() == "exotel.stream_url_request")
    assert rec.method == "POST"
    assert set(rec.fields) == {"CallSid", "From", "CustomField"}
    assert rec.fields["From"] == "*******2532"
    assert "09172522532" not in str(rec.__dict__)
    assert rec.resolved == "c_w:a"
