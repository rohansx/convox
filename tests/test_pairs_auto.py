"""Per-language side defaults and the effective pair for lang "auto" (config/pairs.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dubhash.config.pairs import (
    auto_pair,
    auto_side,
    default_side,
    effective_pair,
    language_sides,
    load_pairs,
)
from dubhash.interp.langid import SUPPORTED_LANGS

PAIRS = load_pairs(Path(__file__).resolve().parents[1] / "dubhash" / "config" / "pairs.yaml")


def test_every_supported_language_has_a_side_in_pairs_yaml():
    sides = language_sides(PAIRS)
    for lang in SUPPORTED_LANGS:
        assert lang in sides, lang
        assert sides[lang].lang == lang
        assert sides[lang].tts == "bulbul"
    assert sides["kn-IN"].voice_f == "bulbul:kn-female-1"


def test_fake_pairs_are_not_used_for_language_sides():
    sides = language_sides(PAIRS)
    assert sides["hi-IN"].stt != "fake"


def test_default_side_follows_the_provider_convention():
    s = default_side("te-IN")
    assert (s.lang, s.stt, s.tts, s.voice_m) == ("te-IN", "sarvam", "bulbul", "bulbul:te-male-1")


def test_auto_side_speaks_through_the_english_pivot():
    s = auto_side("ta-IN", language_sides(PAIRS))
    assert (s.lang, s.stt, s.mt_src) == ("ta-IN", "sarvam-translate", "en-IN")
    assert s.voice_m == "bulbul:ta-male-1"


def test_effective_pair_keeps_the_fixed_side_and_rebuilds_the_auto_side():
    base = auto_pair(PAIRS, {"a": "hi-IN", "b": "auto"}, {})
    sides = language_sides(PAIRS)
    p = effective_pair(base, {"b": "gu-IN"}, sides)
    assert p.a == base.a
    assert (p.b.lang, p.b.stt, p.b.mt_src, p.b.voice_f) == (
        "gu-IN",
        "sarvam-translate",
        "en-IN",
        "bulbul:gu-female-1",
    )
    # pair-level knobs stay those of the call
    assert (p.key, p.eou_ms, p.early_final_ms) == (base.key, base.eou_ms, base.early_final_ms)


def test_effective_pair_keeps_a_side_stt_from_the_base_pair():
    """A side's STT provider is built once per call: retargeting never changes it."""
    base = PAIRS["mr-en"]
    p = effective_pair(base, {"a": "kn-IN", "b": "hi-IN"}, language_sides(PAIRS))
    assert (p.a.lang, p.a.stt) == ("kn-IN", base.a.stt)
    assert (p.b.lang, p.b.stt, p.b.mt_src) == ("hi-IN", base.b.stt, base.b.mt_src)


def test_auto_pair_for_an_auto_side_and_a_fixed_side():
    p = auto_pair(PAIRS, {"a": "auto", "b": "kn-IN"}, {"a": "hi-IN"})
    assert p.key == "auto-kn"
    assert (p.a.lang, p.a.stt, p.a.mt_src) == ("hi-IN", "sarvam-translate", "en-IN")
    assert p.b == PAIRS["hi-kn"].b
    assert p.early_final_ms == PAIRS["hi-kn"].early_final_ms  # knobs of the hinted pair


def test_auto_pair_without_a_hint_starts_in_hindi_with_pivot_defaults():
    p = auto_pair(PAIRS, {"a": "ta-IN", "b": "auto"}, {})
    assert p.key == "ta-auto"
    assert (p.b.lang, p.b.stt) == ("hi-IN", "sarvam-translate")
    assert p.a.lang == "ta-IN"


def test_auto_pair_with_both_sides_auto():
    p = auto_pair(PAIRS, {"a": "auto", "b": "auto"}, {"b": "mr-IN"})
    assert p.key == "auto-auto"
    assert (p.a.lang, p.b.lang) == ("hi-IN", "mr-IN")
    assert p.a.stt == p.b.stt == "sarvam-translate"
    assert p.early_final_ms == 200  # the pivot pattern (L4/L6)


def test_auto_pair_rejects_an_unsupported_fixed_language():
    with pytest.raises(ValueError):
        auto_pair(PAIRS, {"a": "auto", "b": "bn-IN"}, {})
