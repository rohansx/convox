"""CallSession opening_line modes (L7): "full" (the disclosure sentence, plus the record
sentence when record=true), "short" (the two language names and "speak") and "off"."""

from __future__ import annotations

import asyncio

import pytest

from convox.providers.fakes import FakeTTS
from convox.session.prompts import opening_text, short_opening_text
from tests.session.test_call_session import PAIR, attach_both, make, providers


async def _opening_requests(mode, record=False) -> dict[str, list[str]]:
    tts = {"a": FakeTTS(), "b": FakeTTS()}
    session, legs, clock, bus = make(prov=providers(tts=tts), opening_line=mode, record=record)
    await attach_both(session, legs)
    for _ in range(20):
        await asyncio.sleep(0)
    await session.hangup()
    return {s: [r[0] for r in t.requests] for s, t in tts.items()}


async def test_short_plays_the_short_line_once_per_side():
    got = await _opening_requests("short", record=True)
    a, b = PAIR.a.lang, PAIR.b.lang
    assert got == {"a": [short_opening_text(a, b)], "b": [short_opening_text(b, a)]}


async def test_full_plays_the_disclosure_with_the_record_sentence():
    got = await _opening_requests("full", record=True)
    a, b = PAIR.a.lang, PAIR.b.lang
    assert got == {"a": [opening_text(a, b, record=True)], "b": [opening_text(b, a, record=True)]}


async def test_off_plays_nothing():
    assert await _opening_requests("off") == {"a": [], "b": []}


@pytest.mark.parametrize(("flag", "mode"), [(True, "full"), (False, "off")])
def test_booleans_still_mean_full_and_off(flag, mode):
    session, *_ = make(opening_line=flag)
    assert session.opening_line == mode


def test_unknown_mode_is_an_error():
    with pytest.raises(ValueError):
        make(opening_line="long")
