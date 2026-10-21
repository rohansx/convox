"""TTS audio cache (providers/tts_cache.py): a hit streams the stored PCM with no vendor
call; a miss streams from the inner TTS and stores the audio once it is complete."""

from __future__ import annotations

import asyncio
from pathlib import Path

from dubhash.clock import FakeClock
from dubhash.interp.contracts import TTS
from dubhash.providers.fakes import FakeTTS
from dubhash.providers.tts_cache import CachedTTS, TTSAudioCache, tts_key


async def collect(it) -> bytes:
    return b"".join([c async for c in it])


async def test_satisfies_the_tts_protocol():
    tts = CachedTTS(FakeTTS(), TTSAudioCache())
    assert isinstance(tts, TTS)
    assert tts.name == "fake"


async def test_miss_then_hit_skips_the_inner_tts():
    inner = FakeTTS()
    tts = CachedTTS(inner, TTSAudioCache())
    a = await collect(tts.synth("theek hai", "v1", "hi-IN"))
    b = await collect(tts.synth("theek hai", "v1", "hi-IN"))
    assert a == b and a
    assert len(inner.requests) == 1
    assert (tts.store.hits, tts.store.misses) == (1, 1)
    assert tts.has("theek hai", "v1", "hi-IN")


async def test_key_includes_voice_and_lang():
    inner = FakeTTS()
    tts = CachedTTS(inner, TTSAudioCache())
    await collect(tts.synth("haan", "v1", "hi-IN"))
    await collect(tts.synth("haan", "v2", "hi-IN"))
    await collect(tts.synth("haan", "v1", "mr-IN"))
    assert len(inner.requests) == 3


def test_key_normalisation_keeps_meaning():
    # whitespace and a final full stop do not change what is said ...
    assert tts_key(" OTP  batao. ", "v", "hi-IN") == tts_key("OTP batao", "v", "hi-IN")
    assert tts_key("ठीक है।", "v", "hi-IN") == tts_key("ठीक है", "v", "hi-IN")
    # ... a question mark and letter case (OTP read as letters vs "otp") may
    assert tts_key("theek hai?", "v", "hi-IN") != tts_key("theek hai", "v", "hi-IN")
    assert tts_key("OTP", "v", "hi-IN") != tts_key("otp", "v", "hi-IN")


async def test_hit_has_zero_ttfb_on_the_clock():
    clock = FakeClock()
    inner = FakeTTS(ttfb_ms=300, clock=clock)
    tts = CachedTTS(inner, TTSAudioCache())
    task = asyncio.create_task(tts.prefill("2 minute", "v", "hi-IN"))
    for _ in range(5):
        await asyncio.sleep(0)
    clock.advance(300)  # the miss pays the vendor's ttfb on the clock
    await task
    it = tts.synth("2 minute", "v", "hi-IN")
    t0 = clock.now_ms()
    first = await anext(it)
    assert first and clock.now_ms() - t0 == 0
    await it.aclose()


async def test_abandoned_stream_is_not_cached():
    inner = FakeTTS(ms_per_char=200)  # several chunks
    tts = CachedTTS(inner, TTSAudioCache())
    it = tts.synth("lift kaam nahi kar rahi", "v", "hi-IN")
    await anext(it)
    await it.aclose()
    assert not tts.has("lift kaam nahi kar rahi", "v", "hi-IN")
    full = await collect(tts.synth("lift kaam nahi kar rahi", "v", "hi-IN"))
    assert len(full) == len("lift kaam nahi kar rahi") * 200 * 32
    assert tts.has("lift kaam nahi kar rahi", "v", "hi-IN")


async def test_failed_stream_is_not_cached():
    class Broken(FakeTTS):
        async def synth(self, text, voice, lang):
            yield b"\x00\x01" * 100
            raise RuntimeError("socket died")

    tts = CachedTTS(Broken(), TTSAudioCache())
    try:
        await collect(tts.synth("haan", "v", "hi-IN"))
    except RuntimeError:
        pass
    assert not tts.has("haan", "v", "hi-IN")


async def test_lru_is_bounded_by_bytes():
    inner = FakeTTS(ms_per_char=10)  # 10 chars -> 100 ms -> 3200 bytes
    store = TTSAudioCache(max_bytes=3 * 3200)
    tts = CachedTTS(inner, store)
    for t in ("aaaaaaaaaa", "bbbbbbbbbb", "cccccccccc"):
        await collect(tts.synth(t, "v", "hi-IN"))
    await collect(tts.synth("aaaaaaaaaa", "v", "hi-IN"))  # a is most recent now
    await collect(tts.synth("dddddddddd", "v", "hi-IN"))  # evicts b
    assert store.bytes <= 3 * 3200
    assert store.evictions == 1
    assert tts.has("aaaaaaaaaa", "v", "hi-IN") and not tts.has("bbbbbbbbbb", "v", "hi-IN")


async def test_an_entry_larger_than_the_cache_is_not_kept():
    store = TTSAudioCache(max_bytes=1000)
    tts = CachedTTS(FakeTTS(), store)
    await collect(tts.synth("a long sentence", "v", "hi-IN"))
    assert store.bytes == 0 and not tts.has("a long sentence", "v", "hi-IN")


async def test_disk_cache_survives_a_restart(tmp_path: Path):
    inner = FakeTTS()
    tts = CachedTTS(inner, TTSAudioCache(disk_dir=tmp_path))
    pcm = await tts.prefill("main gate ke bahar hoon", "v", "hi-IN", persist=True)
    assert pcm and len(list(tmp_path.glob("*.pcm"))) == 1
    # a new process: empty memory, same directory
    inner2 = FakeTTS()
    tts2 = CachedTTS(inner2, TTSAudioCache(disk_dir=tmp_path))
    assert tts2.has("main gate ke bahar hoon", "v", "hi-IN")
    again = await collect(tts2.synth("main gate ke bahar hoon", "v", "hi-IN"))
    assert again == pcm and inner2.requests == []
    assert tts2.store.disk_hits == 1


async def test_only_persisted_entries_go_to_disk(tmp_path: Path):
    """Ordinary turns (user content) stay in memory; only phrase-pack prefill is written."""
    tts = CachedTTS(FakeTTS(), TTSAudioCache(disk_dir=tmp_path))
    await collect(tts.synth("my flat is 302", "v", "en-IN"))
    assert list(tmp_path.glob("*.pcm")) == []
    assert tts.has("my flat is 302", "v", "en-IN")


async def test_prefill_is_a_noop_when_cached():
    inner = FakeTTS()
    tts = CachedTTS(inner, TTSAudioCache())
    await tts.prefill("haan", "v", "hi-IN")
    await tts.prefill("haan", "v", "hi-IN")
    assert len(inner.requests) == 1


async def test_prewarm_and_aclose_pass_through():
    class Warm(FakeTTS):
        warmed: list[str] = []
        closed = 0

        async def prewarm(self, voice: str) -> None:
            self.warmed.append(voice)

        async def aclose(self) -> None:
            self.closed += 1

    inner = Warm()
    tts = CachedTTS(inner, TTSAudioCache())
    await tts.prewarm("v")
    await tts.aclose()
    assert inner.warmed == ["v"] and inner.closed == 1
