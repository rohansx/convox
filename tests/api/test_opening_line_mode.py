"""Which opening line a call gets (L7): "short" for loopback and the /join stage demo,
"full" for PSTN calls, both overridable in settings; a loopback request may pick its own."""

from __future__ import annotations

import pytest

from tests.api import test_calls
from tests.api.test_loopback import Env, _close_clients  # noqa: F401
from tests.join.test_routes import start

LB = {"a_lang": "mr-IN", "b_lang": "hi-IN"}


def test_loopback_calls_get_the_short_opening_by_default():
    env = Env()
    assert env.client.post("/loopback/calls", json=LB).status_code == 201
    assert env.sessions[0].kw["opening_line"] == "short"


@pytest.mark.parametrize("mode", ["short", "full", "off"])
def test_a_loopback_request_can_pick_the_opening(mode):
    env = Env()
    assert env.client.post("/loopback/calls", json={**LB, "opening_line": mode}).status_code == 201
    assert env.sessions[0].kw["opening_line"] == mode


def test_a_bad_opening_mode_is_rejected():
    env = Env()
    r = env.client.post("/loopback/calls", json={**LB, "opening_line": "long"})
    assert r.status_code == 400


def test_loopback_default_comes_from_settings():
    env = Env(loopback_opening_line="off")
    env.client.post("/loopback/calls", json=LB)
    assert env.sessions[0].kw["opening_line"] == "off"


def test_join_stage_calls_get_the_short_opening():
    env = Env()
    assert start(env, follow_redirects=False).status_code == 303
    assert env.sessions[0].kw["opening_line"] == "short"


async def test_pstn_calls_keep_the_full_opening():
    env = test_calls.Env()
    async with env.client() as c:
        r = await c.post("/v1/calls", json=test_calls.BODY)
    assert r.status_code == 201
    assert env.sessions[0].kw["opening_line"] == "full"


async def test_pstn_opening_comes_from_settings():
    env = test_calls.Env(pstn_opening_line="short")
    async with env.client() as c:
        await c.post("/v1/calls", json=test_calls.BODY)
    assert env.sessions[0].kw["opening_line"] == "short"
