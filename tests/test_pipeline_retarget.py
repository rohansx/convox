"""Interpreter.retarget: a call with lang "auto" changes a side's languages mid-call; the
next utterance uses them (STT language, MT target and skip, TTS voice and language)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from dubhash.clock import FakeClock
from dubhash.config.pairs import auto_pair, effective_pair, language_sides, load_pairs
from dubhash.providers.fakes import FakeMT, FakeSTT, FakeTTS
from tests.audio_fixtures import conversation
from tests.test_pipeline import feed, make

PAIRS = load_pairs(Path(__file__).resolve().parent.parent / "dubhash" / "config" / "pairs.yaml")
SIDES = language_sides(PAIRS)
UTT = conversation([("silence", 300), ("speech", 1200), ("silence", 1000)])
BASE = replace(auto_pair(PAIRS, {"a": "auto", "b": "auto"}, {}), early_final_ms=0)


async def test_retarget_changes_the_next_utterances_languages_and_voice():
    clock = FakeClock()
    stt, mt, tts = FakeSTT(["first line", "second line"]), FakeMT(), FakeTTS()
    interp, sink = make(clock, stt=stt, mt=mt, tts=tts, pair=BASE, gender="f")
    await interp.run(feed(UTT, clock))
    interp.retarget(effective_pair(BASE, {"a": "ta-IN", "b": "kn-IN"}, SIDES), stt_lang="ta-IN")
    await interp.run(feed(UTT, clock))

    assert stt.langs == ["hi-IN", "ta-IN"]
    assert [t.tgt_text for t in sink.translations] == [
        "[hi-IN] first line",
        "[kn-IN] second line",
    ]
    assert [(v, lang) for _, v, lang in tts.requests] == [
        ("bulbul:hi-female-1", "hi-IN"),
        ("bulbul:kn-female-1", "kn-IN"),
    ]


async def test_retarget_to_an_english_listener_skips_mt_on_the_pivot():
    clock = FakeClock()
    mt = FakeMT()
    interp, sink = make(clock, stt=FakeSTT(["hello there"]), mt=mt, pair=BASE)
    interp.retarget(effective_pair(BASE, {"b": "en-IN"}, SIDES))
    await interp.run(feed(UTT, clock))
    assert mt.calls == 0
    assert sink.translations[0].tgt_text == "hello there"


async def test_retarget_without_stt_lang_keeps_the_stt_language():
    clock = FakeClock()
    stt = FakeSTT(["x y"])
    interp, _ = make(clock, stt=stt, tts=FakeTTS(), pair=BASE)
    interp.retarget(effective_pair(BASE, {"a": "mr-IN"}, SIDES))
    await interp.run(feed(UTT, clock))
    assert stt.langs == ["hi-IN"]
