import asyncio
import time

import numpy as np

from dubhash.audio.frames import FRAME_BYTES
from dubhash.interp.contracts import MT, STT, TTS, VAD
from dubhash.providers.fakes import EnergyVAD, FakeMT, FakeSTT, FakeTTS
from tests.audio_fixtures import frames, silence, speech


def test_fakes_satisfy_protocols():
    assert isinstance(FakeSTT(), STT)
    assert isinstance(FakeMT(), MT)
    assert isinstance(FakeTTS(), TTS)
    assert isinstance(EnergyVAD(), VAD)
    assert FakeSTT().name == FakeMT().name == FakeTTS().name == "fake"


async def _feed(stt: FakeSTT, ms: int) -> str:
    s = await stt.open("hi-IN")
    for f in frames(speech(ms)):
        await s.push(f)
    try:
        return await s.end()
    finally:
        await s.aclose()


async def test_fake_stt_words_per_300ms():
    stt = FakeSTT()
    assert await _feed(stt, 900) == "w1 w2 w3"
    assert await _feed(stt, 1000) == "w1 w2 w3"
    assert await _feed(stt, 200) == "w1"


async def test_fake_stt_empty_under_100ms():
    assert await _feed(FakeSTT(), 80) == ""


async def test_fake_stt_scripted_then_fallback():
    stt = FakeSTT(script=["haan", "main gate pe hoon"])
    assert await _feed(stt, 400) == "haan"
    assert await _feed(stt, 400) == "main gate pe hoon"
    assert await _feed(stt, 600) == "w1 w2"


async def test_fake_stt_counts_opens():
    stt = FakeSTT()
    await _feed(stt, 400)
    await _feed(stt, 400)
    assert stt.opened == 2
    assert stt.langs == ["hi-IN", "hi-IN"]


async def test_fake_stt_partials_grow():
    stt = FakeSTT()
    s = await stt.open("hi-IN")
    for f in frames(speech(900)):
        await s.push(f)
    final = await s.end()
    parts = [p async for p in s.partials()]
    assert parts == ["w1", "w1 w2", "w1 w2 w3"]
    assert parts[-1] == final


async def test_fake_stt_final_delay_uses_sleep():
    stt = FakeSTT(final_delay_ms=30)
    s = await stt.open("hi-IN")
    await s.push(speech(400))
    t0 = time.monotonic()
    await s.end()
    assert time.monotonic() - t0 >= 0.025


async def test_fake_mt_deterministic_and_override():
    mt = FakeMT()
    assert await mt.translate("namaste", "hi-IN", "kn-IN") == "[kn-IN] namaste"
    mt2 = FakeMT(dictionary={"namaste": "namaskara"})
    assert await mt2.translate("namaste", "hi-IN", "kn-IN") == "namaskara"
    assert await mt2.translate("haan", "hi-IN", "kn-IN") == "[kn-IN] haan"
    assert mt2.calls == 2


async def test_fake_tts_length_and_chunks():
    tts = FakeTTS(ms_per_char=60, chunk_ms=100)
    chunks = [c async for c in tts.synth("abcdefghij", "fake:m", "kn-IN")]  # 600 ms
    total = b"".join(chunks)
    assert len(total) == 600 * 32
    assert all(len(c) % 2 == 0 for c in chunks)
    assert [len(c) for c in chunks] == [3200] * 6
    x = np.frombuffer(total, dtype=np.int16)
    assert 0 < np.abs(x).max() < 8000  # quiet but not silent


async def test_fake_tts_ttfb_sleeps():
    tts = FakeTTS(ttfb_ms=30)
    t0 = time.monotonic()
    it = tts.synth("ab", "fake:f", "hi-IN")
    await anext(it)
    assert time.monotonic() - t0 >= 0.025


async def test_fake_tts_empty_text_yields_nothing():
    assert [c async for c in FakeTTS().synth("", "fake:m", "kn-IN")] == []


async def test_fake_tts_concurrent_streams_independent():
    tts = FakeTTS()
    outs = await asyncio.gather(
        *[_collect(tts.synth("x" * 5, "fake:m", "kn-IN")) for _ in range(3)]
    )
    assert all(len(o) == 300 * 32 for o in outs)


async def _collect(it) -> bytes:
    return b"".join([c async for c in it])


def test_energy_vad_speech_vs_silence():
    vad = EnergyVAD(threshold_dbfs=-40)
    assert vad.speech_prob(silence(20)) < 0.5
    assert vad.speech_prob(silence(20, noise=30)) < 0.5
    assert vad.speech_prob(speech(200)[FRAME_BYTES * 3 : FRAME_BYTES * 4]) > 0.5
    vad.reset()


def test_energy_vad_empty_frame_is_silence():
    assert EnergyVAD().speech_prob(b"") == 0.0
