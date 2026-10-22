import dataclasses

import pytest

from convox.interp.langid import (
    SUPPORTED_LANGS,
    LangGuess,
    LanguageID,
    pick,
)
from convox.providers.fakes import FakeLID

PCM = b"\x01\x00" * 16000


def test_supported_langs_are_the_measured_six():
    assert SUPPORTED_LANGS == ("hi-IN", "mr-IN", "gu-IN", "kn-IN", "ta-IN", "en-IN")


def test_lang_guess_is_a_frozen_value():
    g = LangGuess(lang="hi-IN", confidence=0.9, source="lid")
    with pytest.raises(dataclasses.FrozenInstanceError):
        g.lang = "mr-IN"  # type: ignore[misc]
    assert not hasattr(g, "__dict__")  # slots


def test_pick_keeps_a_confident_candidate():
    assert pick("mr-IN", 0.97, ["hi-IN", "mr-IN"], threshold=0.8) == LangGuess("mr-IN", 0.97, "lid")


def test_pick_drops_a_guess_below_the_threshold():
    assert pick("mr-IN", 0.6, ["hi-IN", "mr-IN"], threshold=0.8) == LangGuess(None, 0.6, "lid")


def test_pick_drops_a_language_outside_the_candidates():
    # e.g. Sarvam hears Nepali in a Hindi sentence: never claim it, never map it
    assert pick("ne-IN", 0.99, ["hi-IN", "mr-IN"], threshold=0.8) == LangGuess(None, 0.99, "lid")
    assert pick(None, 0.0, SUPPORTED_LANGS, threshold=0.8) == LangGuess(None, 0.0, "lid")


def test_pick_normalises_odia_and_case():
    assert pick("or-IN", 0.9, ["od-IN"], threshold=0.5).lang == "od-IN"
    assert pick("HI-in", 0.9, ["hi-IN"], threshold=0.5).lang == "hi-IN"


def test_pick_passes_the_source_through():
    assert pick("hi-IN", 0.9, ["hi-IN"], threshold=0.5, source="stt_final").source == "stt_final"


async def test_fake_lid_returns_scripted_guesses_in_order_then_none():
    lid = FakeLID([LangGuess("mr-IN", 0.95, "lid"), LangGuess(None, 0.3, "lid")])
    assert isinstance(lid, LanguageID)
    assert lid.name == "fake"
    assert await lid.identify(PCM, ["hi-IN", "mr-IN"]) == LangGuess("mr-IN", 0.95, "lid")
    assert await lid.identify(PCM, ["hi-IN", "mr-IN"]) == LangGuess(None, 0.3, "lid")
    assert await lid.identify(PCM, ["hi-IN"]) == LangGuess(None, 0.0, "lid")
    assert lid.calls == [
        (len(PCM), ("hi-IN", "mr-IN")),
        (len(PCM), ("hi-IN", "mr-IN")),
        (len(PCM), ("hi-IN",)),
    ]


async def test_fake_lid_default_guess_and_candidate_restriction():
    lid = FakeLID(default=LangGuess("ta-IN", 0.9, "lid"))
    assert (await lid.identify(PCM, SUPPORTED_LANGS)).lang == "ta-IN"
    # a scripted language outside the candidates is never returned
    assert (await lid.identify(PCM, ["hi-IN"])).lang is None


async def test_fake_lid_delay_sleeps_on_the_clock():
    class Clock:
        slept: list[int] = []

        async def sleep_ms(self, ms: int) -> None:
            self.slept.append(ms)

    clock = Clock()
    lid = FakeLID(delay_ms=250, clock=clock)
    await lid.identify(PCM, ["hi-IN"])
    assert clock.slept == [250]
