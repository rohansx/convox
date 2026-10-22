import json

import httpx
import pytest

from convox.interp.contracts import TTS
from convox.providers.eleven_tts import ElevenTTS, vendor_voice_id
from convox.providers.registry import ProviderError


class _Chunked(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for c in self._chunks:
            yield c


def _tts(handler) -> ElevenTTS:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ElevenTTS(api_key="xi-test", client=client, base_url="https://mock.eleven")


async def test_synth_streams_pcm_16k_from_flash_v2_5():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, stream=_Chunked([b"\x01\x00\x02", b"\x00", b"\x03\x00"]))

    tts = _tts(handler)
    assert isinstance(tts, TTS) and tts.name == "eleven"
    chunks = [c async for c in tts.synth("नमस्ते", "eleven:abc123", "hi-IN")]

    # odd-length network chunks are re-aligned to whole int16 samples
    assert all(len(c) % 2 == 0 for c in chunks)
    assert b"".join(chunks) == b"\x01\x00\x02\x00\x03\x00"
    req = seen[0]
    assert req.url.path == "/v1/text-to-speech/abc123/stream"
    assert req.url.params["output_format"] == "pcm_16000"
    assert req.headers["xi-api-key"] == "xi-test"
    body = json.loads(req.content)
    assert body["text"] == "नमस्ते"
    assert body["model_id"] == "eleven_flash_v2_5"
    assert body["language_code"] == "hi"


async def test_http_error_raises_provider_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad key"})

    with pytest.raises(ProviderError):
        [c async for c in _tts(handler).synth("x", "eleven:abc", "hi-IN")]


async def test_empty_text_yields_nothing():
    def handler(req: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError

    assert [c async for c in _tts(handler).synth("  ", "eleven:abc", "hi-IN")] == []


def test_voice_mapping_accepts_raw_ids_and_placeholder_aliases():
    assert vendor_voice_id("eleven:KYiVPerWcenyBTIvWbfY") == "KYiVPerWcenyBTIvWbfY"
    assert vendor_voice_id("eleven:hi-female-1") == "KYiVPerWcenyBTIvWbfY"
    assert vendor_voice_id("eleven:hi-male-1") == "jjlrvsvJ1M0ZMRNgZuLO"
    assert vendor_voice_id("plainid") == "plainid"
