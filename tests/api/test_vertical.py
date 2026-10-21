"""Per-call `vertical` (L10): which phrase pack a call prefills. Optional on POST /v1/calls,
POST /loopback/calls and the /join form; "delivery" when not given."""

from __future__ import annotations

from types import SimpleNamespace

from tests.api import test_calls
from tests.api.test_loopback import Env, _close_clients  # noqa: F401
from tests.join.test_routes import start
from tests.session.test_call_session import PAIR

LB = {"a_lang": "mr-IN", "b_lang": "hi-IN"}


def test_loopback_defaults_to_delivery():
    env = Env()
    assert env.client.post("/loopback/calls", json=LB).status_code == 201
    assert env.sessions[0].kw["vertical"] == "delivery"


def test_loopback_can_pick_the_clinic_pack():
    env = Env()
    assert env.client.post("/loopback/calls", json={**LB, "vertical": "clinic"}).status_code == 201
    assert env.sessions[0].kw["vertical"] == "clinic"


def test_an_unknown_vertical_is_rejected():
    env = Env()
    r = env.client.post("/loopback/calls", json={**LB, "vertical": "plumbing"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_body"


def test_join_form_passes_the_vertical():
    env = Env()
    assert start(env, follow_redirects=False).status_code == 303
    assert env.sessions[0].kw["vertical"] == "delivery"


async def test_pstn_calls_take_a_vertical():
    env = test_calls.Env()
    async with env.client() as c:
        r = await c.post("/v1/calls", json={**test_calls.BODY, "vertical": "clinic"})
    assert r.status_code == 201
    assert env.sessions[0].kw["vertical"] == "clinic"


async def test_pstn_default_is_delivery():
    env = test_calls.Env()
    async with env.client() as c:
        await c.post("/v1/calls", json=test_calls.BODY)
    assert env.sessions[0].kw["vertical"] == "delivery"


async def test_the_session_factory_builds_the_pack_and_starts_the_prefill():
    from dubhash.api.deps import get_session_factory
    from dubhash.config.settings import Settings
    from dubhash.events import EventBus

    settings = Settings(_env_file=None, dubhash_env="test", next_turn_prewarm=False)  # type: ignore[call-arg]
    state = SimpleNamespace(settings=settings, bus=EventBus(), writer=None)
    factory = get_session_factory(SimpleNamespace(app=SimpleNamespace(state=state)))  # type: ignore[arg-type]
    session = factory("c_x", PAIR, vertical="clinic")
    assert session.phrases is not None and session.phrases.vertical == "clinic"
    assert session.prefill_task is not None
    assert session.next_turn_prewarm is False
    await session.hangup()
    assert session.prefill_task.done()
