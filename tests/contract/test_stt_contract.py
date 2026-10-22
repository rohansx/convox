from collections.abc import Callable

import pytest

from convox.interp.contracts import STT, STTStream
from convox.providers.fakes import FakeSTT
from tests.audio_fixtures import frames, silence, speech

IMPLS: list = [
    pytest.param(lambda: FakeSTT(), id="fake"),
]


@pytest.fixture(params=IMPLS)
def stt(request) -> STT:
    factory: Callable[[], STT] = request.param
    return factory()


async def test_satisfies_protocol(stt: STT):
    assert isinstance(stt, STT)
    assert isinstance(stt.name, str) and stt.name


async def test_open_push_end_returns_str(stt: STT):
    s = await stt.open("hi-IN")
    assert isinstance(s, STTStream)
    try:
        for f in frames(silence(200) + speech(1200) + silence(300)):
            await s.push(f)
        final = await s.end()
        assert isinstance(final, str)
    finally:
        await s.aclose()


async def test_silence_only_returns_str(stt: STT):
    s = await stt.open("hi-IN")
    try:
        for f in frames(silence(400)):
            await s.push(f)
        assert isinstance(await s.end(), str)
    finally:
        await s.aclose()


async def test_partials_are_strings_and_terminate(stt: STT):
    s = await stt.open("hi-IN")
    try:
        for f in frames(speech(900)):
            await s.push(f)
        await s.end()
        await s.aclose()
        async for p in s.partials():
            assert isinstance(p, str)
    finally:
        await s.aclose()


async def test_aclose_without_end_is_safe(stt: STT):
    s = await stt.open("kn-IN")
    await s.push(speech(100))
    await s.aclose()
    await s.aclose()
