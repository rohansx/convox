import pytest

from convox.clock import FakeClock
from convox.config.pairs import PairConfig
from convox.config.settings import Settings
from convox.interp.mt_cache import CachedMT
from convox.providers.fakes import FakeMT, FakeSTT, FakeTTS
from convox.ward.service import (
    WardProviders,
    WardSession,
    build_ward_providers,
    check_numbers,
    extract_numbers,
    ward_pair,
)
from tests.audio_fixtures import harmonic

PRESS = harmonic(1200, f0=120.0)  # 1.2 s, a low (male) voice
HIGH_PRESS = harmonic(1200, f0=230.0)


def fakes(script=None, dictionary=None, **tts_kw):
    return WardProviders(
        stt={"a": FakeSTT(script=script), "b": FakeSTT(script=script)},
        mt=FakeMT(dictionary=dictionary),
        tts={"a": FakeTTS(**tts_kw), "b": FakeTTS(**tts_kw)},
    )


# --- pair resolution -----------------------------------------------------------------


def test_ward_pair_defaults_to_marathi_hindi_from_pairs_yaml():
    pair = ward_pair("mr-IN", "hi-IN")
    assert isinstance(pair, PairConfig)
    assert pair.a.lang == "mr-IN" and pair.b.lang == "hi-IN"
    assert pair.a.tts == "bulbul" and pair.b.tts == "bulbul"
    assert pair.b.voice_m == "bulbul:hi-male-1"


def test_ward_pair_builds_sides_for_reversed_and_unlisted_orders():
    pair = ward_pair("kn-IN", "mr-IN")  # no kn-mr in pairs.yaml: sides come from each lang
    assert pair.a.lang == "kn-IN" and pair.a.tts == "bulbul"
    assert pair.a.voice_f == "bulbul:kn-female-1"
    assert pair.b.lang == "mr-IN"


def test_ward_pair_synthesises_a_language_missing_from_pairs_yaml():
    pair = ward_pair("bn-IN", "en-IN")
    assert pair.a.stt == "sarvam" and pair.a.tts == "bulbul"
    assert pair.a.voice_m == "bulbul:bn-male-1"
    assert pair.b.tts == "bulbul" and pair.b.voice_f == "bulbul:en-female-1"


def test_ward_pair_rejects_same_or_unknown_language():
    with pytest.raises(ValueError):
        ward_pair("hi-IN", "hi-IN")
    with pytest.raises(ValueError):
        ward_pair("xx-IN", "hi-IN")


def test_build_ward_providers_gives_fakes_in_test_env():
    s = Settings(_env_file=None, convox_env="test")
    p = build_ward_providers(ward_pair("mr-IN", "hi-IN"), s)
    assert isinstance(p.stt["a"], FakeSTT) and isinstance(p.stt["b"], FakeSTT)
    assert isinstance(p.mt, FakeMT)
    assert isinstance(p.tts["a"], FakeTTS) and isinstance(p.tts["b"], FakeTTS)


# --- numbers -------------------------------------------------------------------------


def test_extract_numbers_handles_times_grouping_and_indic_digits():
    assert extract_numbers("2 tablets at 8 pm") == ["2", "8"]
    assert extract_numbers("at 8:30, take 1,50,000 units") == ["8", "30", "150000"]
    assert extract_numbers("दिवसातून ३ वेळा, ०८ वाजता") == ["3", "8"]
    assert extract_numbers("no numbers here") == []


def test_check_numbers_flags_missing_numbers():
    assert check_numbers("2 tablets at 8 pm", "2 गोळ्या रात्री 8 वाजता") is None
    assert check_numbers("2 tablets at 8 pm", "२ गोळ्या रात्री ८ वाजता") is None
    w = check_numbers("2 tablets at 8 pm", "2 गोळ्या रात्री वाजता")
    assert w == "numbers differ: 8 missing in translation"
    # counts matter: "5 and 5" needs two 5s
    assert (
        check_numbers("5 ml, 5 times", "5 ml, कई बार") == "numbers differ: 5 missing in translation"
    )


# --- utterances ----------------------------------------------------------------------


async def test_handle_utterance_returns_texts_audio_and_timings():
    p = fakes(script=["2 tablets at 8 pm"], dictionary={"2 tablets at 8 pm": "2 गोळ्या 8 वाजता"})
    s = WardSession(("mr-IN", "hi-IN"), p, clock=FakeClock())
    turn = await s.handle_utterance("a", PRESS)
    assert turn.side == "a"
    assert turn.src_text == "2 tablets at 8 pm"
    assert turn.tgt_text == "2 गोळ्या 8 वाजता"
    assert turn.warning is None
    assert len(turn.audio) > 0 and len(turn.audio) % 2 == 0
    assert {"stt_ms", "mt_ms", "tts_ttfb_ms", "tts_ms", "first_audio_ms"} <= set(turn.timings)
    # side a speaks Marathi, the TTS of side b's language (Hindi) speaks it
    tts_b = p.tts["b"]
    assert tts_b.requests == [("2 गोळ्या 8 वाजता", "bulbul:hi-male-1", "hi-IN")]
    assert p.tts["a"].requests == []
    assert p.stt["a"].langs == ["mr-IN"]


async def test_handle_utterance_warns_when_a_number_is_lost():
    p = fakes(script=["2 tablets at 8 pm"], dictionary={"2 tablets at 8 pm": "2 गोळ्या रात्री"})
    s = WardSession(("mr-IN", "hi-IN"), p, clock=FakeClock())
    turn = await s.handle_utterance("a", PRESS)
    assert turn.warning is not None
    assert turn.warning.startswith("numbers differ:") and "8" in turn.warning
    assert turn.numbers == ["2", "8"]


async def test_reverse_direction_uses_side_a_language_and_voice_by_gender():
    p = fakes(script=["भात खाऊ शकतो का"])
    s = WardSession(("mr-IN", "hi-IN"), p, clock=FakeClock())
    turn = await s.handle_utterance("b", HIGH_PRESS)
    assert turn.tgt_text == "[mr-IN] भात खाऊ शकतो का"
    assert p.tts["a"].requests[0][1:] == ("bulbul:mr-female-1", "mr-IN")
    assert p.stt["b"].langs == ["hi-IN"]


async def test_mt_is_cached():
    p = fakes(script=["ok", "ok"])
    s = WardSession(("mr-IN", "hi-IN"), p, clock=FakeClock())
    assert isinstance(s.mt, CachedMT)
    await s.handle_utterance("a", PRESS)
    await s.handle_utterance("a", PRESS)
    assert p.mt.calls == 1


async def test_empty_transcript_is_not_a_turn():
    p = fakes(script=[])
    s = WardSession(("mr-IN", "hi-IN"), p, clock=FakeClock())
    turn = await s.handle_utterance("a", b"\x00\x00" * 160)  # 10 ms: FakeSTT hears nothing
    assert turn.src_text == "" and turn.audio == b""
    assert s.transcript == []


async def test_streaming_utterance_matches_handle_utterance():
    p = fakes(script=["hello"])
    s = WardSession(("mr-IN", "hi-IN"), p, clock=FakeClock())
    utt = await s.start("a")
    for i in range(0, len(PRESS), 640):
        await utt.push(PRESS[i : i + 640])
    turn, audio = await utt.finish_streaming()
    assert turn.src_text == "hello" and turn.audio == b""
    chunks = [c async for c in audio]
    assert b"".join(chunks) == turn.audio and turn.audio
    assert "tts_ms" in turn.timings
    assert p.stt["a"].opened == 1


async def test_cancelled_utterance_leaves_no_turn():
    p = fakes(script=["hello"])
    s = WardSession(("mr-IN", "hi-IN"), p, clock=FakeClock())
    utt = await s.start("a")
    await utt.push(PRESS)
    await utt.cancel()
    assert s.transcript == []


async def test_transcript_export():
    p = fakes(script=["2 tablets at 8 pm", "rice?"], dictionary={"2 tablets at 8 pm": "2 गोळ्या"})
    s = WardSession(("mr-IN", "hi-IN"), p, clock=FakeClock(), session_id="w_test")
    await s.handle_utterance("a", PRESS)
    await s.handle_utterance("b", PRESS)
    d = s.to_dict()
    assert d["session_id"] == "w_test"
    assert d["pair"] == {"a": "mr-IN", "b": "hi-IN"}
    assert [t["side"] for t in d["turns"]] == ["a", "b"]
    first = d["turns"][0]
    assert first["seq"] == 0 and first["src_lang"] == "mr-IN" and first["tgt_lang"] == "hi-IN"
    assert first["src_text"] == "2 tablets at 8 pm" and first["tgt_text"] == "2 गोळ्या"
    assert first["warning"] and "8" in first["warning"]
    assert "audio" not in first
    text = s.to_text()
    assert "2 tablets at 8 pm" in text and "2 गोळ्या" in text and "numbers differ" in text
    assert "Marathi" in text and "Hindi" in text


# --- pivot sides and the MT skip (L4) ------------------------------------------------


class _SpyMT(FakeMT):
    def __init__(self) -> None:
        super().__init__()
        self.langs: list[tuple[str, str]] = []

    async def translate(self, text: str, src: str, tgt: str) -> str:
        self.langs.append((src, tgt))
        return await super().translate(text, src, tgt)


def spy_fakes(script):
    p = fakes(script=script)
    p.mt = _SpyMT()
    return p


async def test_ward_translates_from_the_sides_mt_lang():
    p = spy_fakes(["where is my parcel"])
    s = WardSession(ward_pair("mr-IN", "hi-IN"), p, clock=FakeClock())
    assert s.pair.a.mt_lang == "en-IN"  # mr-hi pivots: Marathi speech -> English final
    turn = await s.handle_utterance("a", PRESS)
    assert p.mt.langs == [("en-IN", "hi-IN")]
    assert turn.src_lang == "mr-IN" and turn.tgt_lang == "hi-IN"
    assert p.stt["a"].langs == ["mr-IN"]


async def test_ward_skips_mt_when_stt_already_speaks_the_target():
    p = spy_fakes(["I have had a fever for 3 days"])
    s = WardSession(ward_pair("mr-IN", "en-IN"), p, clock=FakeClock())
    turn = await s.handle_utterance("a", PRESS)
    assert p.mt.langs == []  # no vendor call
    assert turn.tgt_text == turn.src_text == "I have had a fever for 3 days"
    assert turn.timings["mt_ms"] == 0 and turn.warning is None
    assert p.tts["b"].requests[0][2] == "en-IN"


async def test_ward_english_on_side_a_uses_the_swapped_mr_en_pair():
    pair = ward_pair("en-IN", "mr-IN")
    assert pair.key == "mr-en" and pair.b.stt == "sarvam-translate"
    p = spy_fakes(["take this tablet twice a day"])
    s = WardSession(pair, p, clock=FakeClock())
    await s.handle_utterance("a", PRESS)
    assert p.mt.langs == [("en-IN", "mr-IN")]


def test_ward_offers_the_marathi_choices():
    from convox.ward.service import DEFAULT_LANGS, PRESETS

    assert DEFAULT_LANGS == PRESETS[0]
    assert ("mr-IN", "hi-IN") in PRESETS and ("mr-IN", "en-IN") in PRESETS
    for a, b in PRESETS:
        ward_pair(a, b)  # each resolves


def test_ward_offers_the_gujarati_choices():
    from convox.ward.service import LANGS, PRESETS

    assert ("gu-IN", "hi-IN") in PRESETS and ("gu-IN", "en-IN") in PRESETS
    assert LANGS["gu-IN"] == ("Gujarati", "ગુજરાતી")
    assert ward_pair("gu-IN", "hi-IN").key == "gu-hi"
    assert ward_pair("en-IN", "gu-IN").key == "gu-en"
