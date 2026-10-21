"""POST /v1/calls and POST /loopback/calls with lang "auto" and lang_hint (api.md)."""

from __future__ import annotations

from tests.api.test_calls import BODY, Env


def leg(lang: str, number: str = "+919876543210", **kw) -> dict:
    return {"number": number, "lang": lang, **kw}


async def test_auto_side_with_a_hint_creates_a_call_with_auto_setup():
    env = Env()
    body = {**BODY, "a": leg("auto", "+919812345678", lang_hint="mr-IN")}
    async with env.client() as c:
        r = await c.post("/v1/calls", json=body)
    assert r.status_code == 201, r.text
    session = env.sessions[0]
    assert session.pair.key == "auto-kn"
    auto = session.kw["auto"]
    assert auto.hints == {"a": "mr-IN"}
    assert session.pair.a.lang == "mr-IN" and session.pair.a.stt == "sarvam-translate"
    call_id = r.json()["call_id"]
    assert env.app.state.registry.get_call(call_id)["a_lang"] == "auto"
    assert env.book.get(call_id).legs["a"].lang == "auto"


async def test_both_sides_auto_without_hints():
    env = Env()
    body = {**BODY, "a": leg("auto", "+919812345678"), "b": leg("auto")}
    async with env.client() as c:
        r = await c.post("/v1/calls", json=body)
    assert r.status_code == 201, r.text
    assert env.sessions[0].kw["auto"].hints == {"a": None, "b": None}


async def test_fixed_pairs_pass_no_auto_setup():
    env = Env()
    async with env.client() as c:
        r = await c.post("/v1/calls", json=BODY)
    assert r.status_code == 201
    assert "auto" not in env.sessions[0].kw


async def test_a_bad_hint_is_400():
    env = Env()
    body = {**BODY, "a": leg("auto", "+919812345678", lang_hint="hindi")}
    async with env.client() as c:
        r = await c.post("/v1/calls", json=body)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_body"


async def test_a_lang_that_is_neither_a_code_nor_auto_is_400():
    env = Env()
    body = {**BODY, "a": leg("detect", "+919812345678")}
    async with env.client() as c:
        r = await c.post("/v1/calls", json=body)
    assert r.status_code == 400


async def test_auto_with_an_unsupported_fixed_side_is_422():
    env = Env()
    body = {**BODY, "a": leg("auto", "+919812345678"), "b": leg("od-IN")}
    async with env.client() as c:
        r = await c.post("/v1/calls", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unsupported_pair"


async def test_an_unsupported_hint_is_accepted_and_ignored():
    env = Env()
    body = {**BODY, "a": leg("auto", "+919812345678", lang_hint="bn-IN")}
    async with env.client() as c:
        r = await c.post("/v1/calls", json=body)
    assert r.status_code == 201
    assert env.sessions[0].pair.a.lang == "hi-IN"  # never claimed: starts in Hindi


async def test_loopback_call_with_an_auto_side_and_hint():
    env = Env()
    async with env.client() as c:
        r = await c.post(
            "/loopback/calls",
            json={"a_lang": "auto", "a_lang_hint": "gu-IN", "b_lang": "hi-IN"},
        )
    assert r.status_code == 201, r.text
    session = env.sessions[0]
    assert session.kw["auto"].hints == {"a": "gu-IN"}
    assert session.kw["co_located"] is True
    assert "lang=auto" in r.json()["a_url"]


async def test_loopback_rejects_a_named_pair_with_an_auto_side():
    env = Env()
    async with env.client() as c:
        r = await c.post(
            "/loopback/calls", json={"a_lang": "auto", "b_lang": "kn-IN", "pair": "hi-kn"}
        )
    assert r.status_code == 422
