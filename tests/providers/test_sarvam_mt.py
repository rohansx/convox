import json

import httpx
import pytest

from dubhash.interp.contracts import MT
from dubhash.providers.registry import ProviderError
from dubhash.providers.sarvam_mt import SarvamMT


def _mt(handler) -> SarvamMT:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SarvamMT(api_key="sk-test", client=client, base_url="https://mock.sarvam")


async def test_translate_posts_colloquial_request_with_digits_kept():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"request_id": "r1", "translated_text": "ನಮಸ್ಕಾರ"})

    mt = _mt(handler)
    assert isinstance(mt, MT)
    assert mt.name == "sarvam"
    out = await mt.translate("namaste", "hi-IN", "kn-IN")

    assert out == "ನಮಸ್ಕಾರ"
    req = seen[0]
    assert req.method == "POST"
    assert str(req.url) == "https://mock.sarvam/translate"
    assert req.headers["api-subscription-key"] == "sk-test"
    body = json.loads(req.content)
    assert body == {
        "input": "namaste",
        "source_language_code": "hi-IN",
        "target_language_code": "kn-IN",
        "mode": "modern-colloquial",
        "model": "mayura:v1",
        "numerals_format": "international",
    }


async def test_empty_text_skips_the_call():
    def handler(req: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request expected")

    assert await _mt(handler).translate("   ", "hi-IN", "kn-IN") == ""


async def test_http_error_raises_provider_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    with pytest.raises(ProviderError):
        await _mt(handler).translate("namaste", "hi-IN", "kn-IN")


async def test_timeout_raises_provider_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(ProviderError):
        await _mt(handler).translate("namaste", "hi-IN", "kn-IN")


async def test_indic_to_indic_rejected_falls_back_to_english_pivot_and_remembers():
    calls: list[tuple[str, str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        b = json.loads(req.content)
        calls.append((b["source_language_code"], b["target_language_code"], b["input"]))
        if b["source_language_code"] != "en-IN" and b["target_language_code"] != "en-IN":
            return httpx.Response(400, json={"error": {"message": "unsupported pair"}})
        return httpx.Response(200, json={"translated_text": f"<{b['input']}>"})

    mt = _mt(handler)
    assert await mt.translate("namaste", "hi-IN", "kn-IN") == "<<namaste>>"
    assert calls == [
        ("hi-IN", "kn-IN", "namaste"),
        ("hi-IN", "en-IN", "namaste"),
        ("en-IN", "kn-IN", "<namaste>"),
    ]
    calls.clear()
    await mt.translate("theek hai", "hi-IN", "kn-IN")
    assert [c[:2] for c in calls] == [("hi-IN", "en-IN"), ("en-IN", "kn-IN")]


async def test_400_with_english_side_is_not_retried():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad"}})

    with pytest.raises(ProviderError):
        await _mt(handler).translate("hello", "en-IN", "kn-IN")


async def test_odia_code_is_passed_as_od_in():
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"translated_text": "x"})

    await _mt(handler).translate("namaste", "hi-IN", "or-IN")
    assert seen[0]["target_language_code"] == "od-IN"
