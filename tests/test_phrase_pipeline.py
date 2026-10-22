"""The interpreter's phrase path (L10): instant acknowledgements on an idle floor, phrase
matches served from the MT and TTS caches, and the number read-back hook."""

from __future__ import annotations

import dataclasses

from convox.clock import FakeClock
from convox.interp.mt_cache import CachedMT
from convox.interp.numbers import readback
from convox.interp.phrases import PhrasePack
from convox.providers.fakes import FakeMT, FakeSTT, FakeTTS
from convox.providers.tts_cache import CachedTTS, TTSAudioCache
from tests.audio_fixtures import conversation
from tests.test_pipeline import PAIR, feed, make

PACK = PhrasePack.from_dict(
    {
        "vertical": "t",
        "lines": [
            {"id": "ok", "ack": True, "hi": ["ठीक है", "theek hai"], "en": ["Okay"]},
            {"id": "otp", "hi": ["OTP बताइए", "OTP batao"], "en": ["Tell me the OTP"]},
        ],
    }
)
SHORT = conversation([("silence", 300), ("speech", 400), ("silence", 800)])  # an ack
LONG = conversation([("silence", 300), ("speech", 1500), ("silence", 800)])


async def warm(text: str, src: str = "hi-IN", tgt: str = "kn-IN"):
    """Caches prefilled with `text` (as session/prefill.py does), counters reset."""
    fmt, ftts = FakeMT(), FakeTTS()
    mt, tts = CachedMT(fmt), CachedTTS(ftts, TTSAudioCache())
    out = await mt.prefill(text, src, tgt)
    for voice in (PAIR.b.voice_m, PAIR.b.voice_f):
        await tts.prefill(out, voice, tgt)
    fmt.calls = 0
    ftts.requests.clear()
    return mt, tts, fmt, ftts


async def run(pcm: bytes, **kw):
    clock = FakeClock()
    phrases = kw.pop("phrases", PACK)
    interp, sink = make(clock, **kw)
    interp.phrases = phrases
    await interp.run(feed(pcm, clock))
    await interp.aclose()
    return interp, sink


async def test_an_ack_on_an_idle_floor_is_a_turn_served_from_the_caches():
    mt, tts, fmt, ftts = await warm("ठीक है")
    _, sink = await run_ack(lambda: True, script="Theek hai.", mt=mt, tts=tts)
    (tr,) = sink.translations
    assert tr.src_text == "Theek hai."
    assert tr.tgt_text == "[kn-IN] ठीक है"  # the canonical line's translation
    assert fmt.calls == 0 and ftts.requests == []  # no MT, no TTS: cache only
    t = tr.timings
    assert (t["phrase_hit"], t["ack"], t["mt_cache_hit"], t["tts_cache_hit"]) == (1, 1, 1, 1)
    assert t["t_tts_ttfb"] - t["t_stt_final"] == 0
    assert sink.audio[0]


async def run_ack(ack_ok, script="theek hai", **kw):
    clock = FakeClock()
    interp, sink = make(clock, stt=FakeSTT(script=[script]), **kw)
    interp.phrases = PACK
    interp.ack_ok = ack_ok
    await interp.run(feed(SHORT, clock))
    await interp.aclose()
    return interp, sink


async def test_ack_turn_uses_no_mt_or_tts_calls():
    mt, tts, fmt, ftts = await warm("ठीक है")
    _, sink = await run_ack(lambda: True, mt=mt, tts=tts)
    assert len(sink.translations) == 1
    assert fmt.calls == 0 and ftts.requests == []


async def test_an_ack_during_the_other_persons_playback_stays_a_backchannel():
    mt, tts, fmt, _ = await warm("ठीक है")
    _, sink = await run_ack(lambda: False, mt=mt, tts=tts)
    assert sink.translations == [] and sink.kinds()[-1] == "discarded"


async def test_the_floor_is_judged_when_the_ack_starts():
    """ack_ok is asked at speech start: playback ending during the ack does not count."""
    calls: list[int] = []

    def ack_ok() -> bool:
        calls.append(1)
        return len(calls) > 1  # False at speech start, True if asked again later

    _, sink = await run_ack(ack_ok)
    assert sink.translations == [] and calls == [1]


async def test_a_short_utterance_not_in_the_pack_is_still_discarded():
    _, sink = await run_ack(lambda: True, script="kya hua")
    assert sink.translations == []


async def test_a_cold_ack_on_an_idle_floor_is_translated_normally():
    fmt, ftts = FakeMT(), FakeTTS()
    _, sink = await run_ack(lambda: True, mt=CachedMT(fmt), tts=CachedTTS(ftts, TTSAudioCache()))
    (tr,) = sink.translations
    assert fmt.texts == ["ठीक है"] and len(ftts.requests) == 1
    assert tr.timings["mt_cache_hit"] == 0 and tr.timings["tts_cache_hit"] == 0


async def test_a_long_phrase_match_translates_the_canonical_text():
    fmt = FakeMT()
    _, sink = await run(LONG, stt=FakeSTT(script=["otp batao"]), mt=CachedMT(fmt))
    (tr,) = sink.translations
    assert fmt.texts == ["OTP बताइए"] and tr.src_text == "otp batao"
    assert tr.timings["phrase_hit"] == 1 and tr.timings["ack"] == 0


async def test_no_match_translates_the_final_as_is():
    fmt = FakeMT()
    _, sink = await run(LONG, stt=FakeSTT(script=["main abhi aa raha hoon"]), mt=fmt)
    (tr,) = sink.translations
    assert fmt.texts == ["main abhi aa raha hoon"] and tr.timings["phrase_hit"] == 0


async def test_without_a_pack_there_is_no_phrase_path():
    _, sink = await run(SHORT, stt=FakeSTT(script=["theek hai"]), phrases=None)
    assert sink.translations == []  # a backchannel, as before L10


async def test_readback_fixes_a_wrong_number_after_mt():
    fixes: list[tuple[str, str]] = []

    def rb(src: str, tgt: str) -> str:
        out, fix = readback(src, tgt, "kn-IN")
        if fix is not None:
            fixes.append((src, out))
        return out

    clock = FakeClock()
    mt = FakeMT(dictionary={"OTP 4729": "OTP 47296"})
    interp, sink = make(clock, stt=FakeSTT(script=["OTP 4729"]), mt=mt)
    interp.readback = rb
    await interp.run(feed(LONG, clock))
    (tr,) = sink.translations
    assert tr.tgt_text == "OTP ನಾಲ್ಕು ಏಳು ಎರಡು ಒಂಬತ್ತು"
    assert tr.timings["number_fix"] == 1 and fixes == [("OTP 4729", tr.tgt_text)]


async def test_readback_on_the_mt_skip_route_is_not_needed():
    """mr->en style: the STT's English is delivered as is; nothing was translated."""
    pair = dataclasses.replace(PAIR, a=dataclasses.replace(PAIR.a, mt_src=PAIR.b.lang))
    called: list[int] = []
    clock = FakeClock()
    interp, sink = make(clock, stt=FakeSTT(script=["OTP 4729"]), pair=pair)
    interp.readback = lambda s, t: called.append(1) or t
    await interp.run(feed(LONG, clock))
    assert sink.translations and called == []
