"""LanguageResolver: the per-side state machine for lang: "auto" (session/language.py)."""

from __future__ import annotations

import pytest

from dubhash.interp.langid import LangGuess
from dubhash.session.language import (
    MENU_DIGITS,
    LangState,
    LanguageResolver,
    ResolverConfig,
    combine,
)

LONG = 2000  # ms of speech: over min_speech_ms
U, P, C = LangState.UNKNOWN, LangState.PROVISIONAL, LangState.CONFIRMED


def lid(lang: str | None, conf: float) -> LangGuess:
    return LangGuess(lang, conf, "lid")


def observe(r: LanguageResolver, lang: str | None, conf: float, **kw):
    return r.observe(speech_ms=kw.pop("speech_ms", LONG), lid=lid(lang, conf), **kw)


# --- starting state ----------------------------------------------------------------


def test_starts_unknown_and_targets_the_hint():
    r = LanguageResolver("a", hint="mr-IN")
    assert (r.state, r.lang, r.hint) == (U, None, "mr-IN")
    assert r.target == "mr-IN"


def test_without_a_hint_the_target_is_hindi():
    r = LanguageResolver("a")
    assert r.target == "hi-IN"


def test_an_unsupported_hint_is_ignored():
    r = LanguageResolver("a", hint="bn-IN")
    assert r.hint is None
    assert r.target == "hi-IN"


# --- confirming --------------------------------------------------------------------


def test_one_very_confident_utterance_confirms():
    r = LanguageResolver("a")
    up = observe(r, "kn-IN", 0.95)
    assert up is not None
    assert (up.side, up.lang, up.state, up.source) == ("a", "kn-IN", C, "lid")
    assert up.confidence == pytest.approx(0.95)
    assert (r.state, r.lang, r.target) == (C, "kn-IN", "kn-IN")


def test_a_confident_guess_is_provisional_then_a_second_agreeing_one_confirms():
    r = LanguageResolver("a")
    up = observe(r, "ta-IN", 0.7)
    assert (up.state, up.lang) == (P, "ta-IN")
    assert r.target == "ta-IN"  # a provisional language is already used
    up = observe(r, "ta-IN", 0.75)
    assert (up.state, up.lang) == (C, "ta-IN")


def test_a_disagreeing_second_guess_stays_provisional_in_the_new_language():
    r = LanguageResolver("a")
    observe(r, "hi-IN", 0.7)
    up = observe(r, "mr-IN", 0.7)
    assert (up.state, up.lang) == (P, "mr-IN")
    up = observe(r, "mr-IN", 0.7)
    assert (up.state, up.lang) == (C, "mr-IN")


def test_short_utterances_are_never_decided_on():
    r = LanguageResolver("a")
    assert observe(r, "kn-IN", 0.99, speech_ms=1499) is None
    assert r.state == U
    assert observe(r, "kn-IN", 0.99, speech_ms=1500) is not None


def test_min_speech_ms_is_configurable():
    r = LanguageResolver("a", config=ResolverConfig(min_speech_ms=800))
    assert observe(r, "kn-IN", 0.99, speech_ms=900).state == C


def test_the_hint_prior_lifts_a_borderline_guess_that_agrees_with_it():
    plain = LanguageResolver("a")
    hinted = LanguageResolver("a", hint="gu-IN")
    assert observe(plain, "gu-IN", 0.55) is None  # under `confident`
    up = observe(hinted, "gu-IN", 0.55)
    assert (up.state, up.lang) == (P, "gu-IN")


def test_a_hint_alone_never_resolves_a_side():
    r = LanguageResolver("a", hint="hi-IN")
    assert r.observe(speech_ms=LONG) is None
    assert r.state == U


def test_stt_final_language_counts_as_evidence():
    r = LanguageResolver("a")
    up = r.observe(speech_ms=LONG, stt_lang="mr-IN")
    assert (up.state, up.lang, up.source) == (P, "mr-IN", "stt_final")


def test_stt_and_lid_agreeing_confirm_at_once():
    r = LanguageResolver("a")
    up = r.observe(speech_ms=LONG, stt_lang="gu-IN", lid=lid("gu-IN", 0.8))
    assert (up.state, up.lang) == (C, "gu-IN")


def test_stt_and_lid_disagreeing_is_low_confidence():
    r = LanguageResolver("a")
    assert r.observe(speech_ms=LONG, stt_lang="hi-IN", lid=lid("mr-IN", 0.8)) is None
    assert r.state == U


def test_stt_language_code_is_normalised():
    r = LanguageResolver("a")
    up = r.observe(speech_ms=LONG, stt_lang="kn-in", lid=lid("kn-IN", 0.8))
    assert up.lang == "kn-IN"


# --- switching a confirmed side ------------------------------------------------------


def confirmed(lang: str = "hi-IN") -> LanguageResolver:
    r = LanguageResolver("a")
    observe(r, lang, 0.95)
    assert r.state == C
    return r


def test_one_utterance_in_another_language_does_not_switch():
    r = confirmed("hi-IN")
    assert observe(r, "mr-IN", 0.99) is None
    assert (r.state, r.lang) == (C, "hi-IN")


def test_two_consecutive_confident_utterances_in_a_new_language_switch():
    r = confirmed("hi-IN")
    observe(r, "mr-IN", 0.8)
    up = observe(r, "mr-IN", 0.8)
    assert (up.state, up.lang) == (C, "mr-IN")


def test_an_utterance_in_the_old_language_resets_the_switch_count():
    r = confirmed("hi-IN")
    observe(r, "mr-IN", 0.8)
    observe(r, "hi-IN", 0.8)
    assert observe(r, "mr-IN", 0.8) is None
    assert r.lang == "hi-IN"


def test_a_low_confidence_utterance_resets_the_switch_count():
    r = confirmed("hi-IN")
    observe(r, "mr-IN", 0.8)
    observe(r, None, 0.2)
    assert observe(r, "mr-IN", 0.8) is None
    assert r.lang == "hi-IN"


def test_code_mixed_english_does_not_flip_an_indic_side():
    """Hindi with English loanwords: LID may hear English, the transcript is still mostly
    Devanagari. Those utterances do not count towards a switch."""
    r = confirmed("hi-IN")
    mixed = "मेरा order number क्या है, delivery कब होगी"
    assert observe(r, "en-IN", 0.97, text=mixed) is None
    assert observe(r, "en-IN", 0.97, text=mixed) is None
    assert observe(r, "en-IN", 0.97, text=mixed) is None
    assert (r.state, r.lang) == (C, "hi-IN")


def test_english_script_loanwords_never_count_as_english():
    assert combine(stt_lang="en-IN", lid=None, text="मेरा order कहाँ है", current="hi-IN")[0] is None


def test_plain_english_still_switches_an_indic_side():
    r = confirmed("hi-IN")
    observe(r, "en-IN", 0.96, text="where is my order")
    up = observe(r, "en-IN", 0.96, text="it was due yesterday")
    assert (up.state, up.lang) == (C, "en-IN")


# --- fallback: the keypad menu --------------------------------------------------------


def test_two_low_confidence_utterances_request_the_menu_once():
    r = LanguageResolver("a")
    assert observe(r, None, 0.3) is None
    up = observe(r, None, 0.4)
    assert up is not None and up.menu
    assert (up.state, up.lang) == (U, None)
    assert r.menu_requested
    assert observe(r, None, 0.4) is None  # once per call
    assert observe(r, None, 0.4) is None


def test_two_unsupported_utterances_request_the_menu():
    r = LanguageResolver("a")
    assert r.observe(speech_ms=LONG, stt_lang="bn-IN") is None
    up = r.observe(speech_ms=LONG, stt_lang="bn-IN")
    assert up.menu
    assert r.lang is None  # never claimed


def test_an_unsupported_language_is_never_claimed_even_when_sure():
    r = LanguageResolver("a")
    r.observe(speech_ms=LONG, stt_lang="te-IN", lid=lid("te-IN", 0.99))
    assert (r.state, r.lang) == (U, None)


def test_a_confident_utterance_resets_the_menu_count():
    r = LanguageResolver("a")
    observe(r, None, 0.3)
    observe(r, "ta-IN", 0.7)
    up = observe(r, None, 0.3)
    assert up is None


def test_a_confirmed_side_never_asks_for_the_menu():
    r = confirmed()
    assert observe(r, None, 0.1) is None
    assert observe(r, None, 0.1) is None
    assert not r.menu_requested


def test_menu_digits_map_to_the_six_languages():
    assert MENU_DIGITS == {
        "1": "hi-IN",
        "2": "mr-IN",
        "3": "gu-IN",
        "4": "kn-IN",
        "5": "ta-IN",
        "6": "en-IN",
    }


def test_a_keypad_choice_confirms_and_pins_the_side():
    r = LanguageResolver("a")
    up = r.choose("gu-IN")
    assert (up.lang, up.state, up.source, up.confidence) == ("gu-IN", C, "dtmf", 1.0)
    # pinned: detection no longer moves it
    observe(r, "hi-IN", 0.95)
    observe(r, "hi-IN", 0.95)
    assert r.lang == "gu-IN"


def test_choose_rejects_an_unsupported_language():
    with pytest.raises(ValueError):
        LanguageResolver("a").choose("bn-IN")


def test_update_payload_is_the_lang_event():
    up = observe(LanguageResolver("b"), "kn-IN", 0.95)
    assert up.payload() == {
        "side": "b",
        "lang": "kn-IN",
        "state": "CONFIRMED",
        "confidence": 0.95,
        "source": "lid",
    }
    menu = LanguageResolver("a")
    observe(menu, None, 0.1)
    assert observe(menu, None, 0.1).payload()["menu"] is True


def test_the_stt_finals_own_confidence_is_used_when_it_has_one():
    """Sarvam realtime with language_code=auto reports language_confidence on finals."""
    sure = LanguageResolver("a")
    up = sure.observe(speech_ms=LONG, stt_lang="mr-IN", stt_confidence=0.97)
    assert (up.state, up.confidence) == (C, pytest.approx(0.97))
    unsure = LanguageResolver("a")
    assert unsure.observe(speech_ms=LONG, stt_lang="mr-IN", stt_confidence=0.4) is None


def test_english_needs_more_confidence_than_an_indic_language():
    """Loanword-only utterances ("Flat number 302") come back en-IN at up to 0.93 (L8)."""
    r = LanguageResolver("a")
    assert observe(r, "en-IN", 0.9) is None
    assert observe(r, "en-IN", 0.93) is not None and r.menu_requested  # two unsure: menu
    r2 = LanguageResolver("a")
    assert observe(r2, "en-IN", 0.96).state == C
    assert ResolverConfig().lang_confident == {"en-IN": 0.95}


def test_lang_is_a_call_event_kind():
    from typing import get_args

    from dubhash.types import EventKind

    assert "lang" in get_args(EventKind)
