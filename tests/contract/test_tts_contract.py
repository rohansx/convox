from collections.abc import Callable

import pytest

from dubhash.interp.contracts import TTS
from dubhash.providers.fakes import FakeTTS

IMPLS: list = [
    pytest.param(lambda: (FakeTTS(), "fake:m"), id="fake"),
]


@pytest.fixture(params=IMPLS)
def tts_voice(request) -> tuple[TTS, str]:
    factory: Callable[[], tuple[TTS, str]] = request.param
    return factory()


async def test_satisfies_protocol(tts_voice):
    tts, _ = tts_voice
    assert isinstance(tts, TTS)
    assert isinstance(tts.name, str) and tts.name


async def test_synth_yields_even_length_pcm16_chunks(tts_voice):
    tts, voice = tts_voice
    chunks = [c async for c in tts.synth("namaskara, naanu gate hattira iddini", voice, "kn-IN")]
    assert chunks, "no audio"
    for c in chunks:
        assert isinstance(c, bytes)
        assert len(c) > 0 and len(c) % 2 == 0
    assert sum(len(c) for c in chunks) >= 16_000 * 2 // 10  # at least 100 ms of audio


async def test_synth_can_be_abandoned_early(tts_voice):
    tts, voice = tts_voice
    it = tts.synth("ondu eradu mooru naalku", voice, "kn-IN")
    first = await anext(it)
    assert len(first) % 2 == 0
    await it.aclose()
