import json

import httpx
import pytest

from tools.call import e164, place_call, platform_key, watch


def test_e164_normalises_indian_numbers():
    assert e164("9172522532") == "+919172522532"
    assert e164("09172522532") == "+919172522532"
    assert e164("+91 91725 22532") == "+919172522532"
    assert e164("09513886363") == "+919513886363"
    with pytest.raises(ValueError):
        e164("12345")


def test_platform_key_takes_first_pair():
    assert platform_key("dev:secret,other:x") == "secret"


async def test_place_call_posts_v1_calls_with_bearer_key():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(201, json={"call_id": "c_1", "status": "ringing"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://app"
    ) as c:
        call_id = await place_call(
            c,
            key="k",
            a=("9172522532", "mr-IN"),
            b=("09876543210", "hi-IN"),
            caller_id="09513886363",
            provider="exotel",
        )
    assert call_id == "c_1"
    req = seen[0]
    assert req.url.path == "/v1/calls" and req.headers["authorization"] == "Bearer k"
    body = json.loads(req.content)
    assert body["a"] == {"number": "+919172522532", "lang": "mr-IN"}
    assert body["b"] == {"number": "+919876543210", "lang": "hi-IN"}
    assert body["caller_id"] == "+919513886363" and body["provider"] == "exotel"
    assert body["record"] is True  # test calls keep transcripts for the turn log


async def test_watch_prints_new_turns_until_ended():
    polls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/turns"):
            turns = [
                {
                    "seq": 0,
                    "side": "a",
                    "src_text": "namaskar",
                    "tgt_text": "namaste",
                    "t_speech_end": 1000,
                    "t_play_start": 2400,
                }
            ]
            return httpx.Response(200, json={"turns": turns[: polls["n"]]})
        polls["n"] += 1
        status = "in_progress" if polls["n"] < 3 else "completed"
        return httpx.Response(200, json={"call_id": "c_1", "status": status})

    lines: list[str] = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://app"
    ) as c:
        final = await watch(c, "c_1", key="k", interval_s=0, out=lines.append)
    assert final["status"] == "completed"
    turn_lines = [ln for ln in lines if "namaskar" in ln]
    assert len(turn_lines) == 1 and "1400 ms" in turn_lines[0]
