"""The short opening line (opening_line: "short", the /join and loopback default): the
two language names and "speak", about 1.5-2 s of Bulbul speech, so a caller who talks
straight away is not held up behind it (open question L7)."""

from __future__ import annotations

import pytest

from convox.session.prompts import opening_text, short_opening_text

LANGS = ["hi-IN", "kn-IN", "ta-IN", "mr-IN", "gu-IN", "en-IN"]


def test_marathi_hindi_and_gujarati_short_lines():
    assert short_opening_text("mr-IN", "hi-IN") == "मराठी ते हिंदी. बोला."
    assert short_opening_text("hi-IN", "mr-IN") == "हिंदी से मराठी. बोलिए."
    assert short_opening_text("gu-IN", "en-IN") == "ગુજરાતી થી અંગ્રેજી. બોલો."
    assert short_opening_text("en-IN", "gu-IN") == "English to Gujarati. Go ahead."


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("other", LANGS)
def test_short_line_is_a_few_words_and_shorter_than_the_full_one(lang, other):
    if lang == other:
        return
    text = short_opening_text(lang, other)
    assert len(text.split()) <= 5, text
    assert len(text) <= 32, (len(text), text)
    assert len(text) < len(opening_text(lang, other))


def test_short_line_never_carries_the_record_sentence():
    assert short_opening_text("mr-IN", "hi-IN") == short_opening_text("mr-IN", "hi-IN")
    assert "save" not in short_opening_text("en-IN", "mr-IN").lower()


def test_unknown_language_falls_back_to_english():
    assert short_opening_text("xx-IN", "hi-IN") == "xx to Hindi. Go ahead."
