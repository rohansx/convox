"""Prompts for lang "auto": the neutral opening, the partner-unknown opening and the
keypad language menu (session/prompts.py)."""

from convox.session.prompts import (
    auto_opening_text,
    language_menu_text,
    partner_auto_opening_text,
)


def test_auto_opening_is_the_hint_language_then_english():
    assert auto_opening_text(None) == (
        "Namaste, apni bhasha mein boliye. Please speak in your language."
    )
    assert auto_opening_text("hi-IN") == auto_opening_text(None)
    mr = auto_opening_text("mr-IN")
    assert mr.endswith("Please speak in your language.")
    assert "बोला" in mr


def test_auto_opening_in_english_is_said_once():
    assert auto_opening_text("en-IN") == "Please speak in your language."


def test_auto_opening_has_a_line_for_every_supported_language():
    from convox.interp.langid import SUPPORTED_LANGS

    for lang in SUPPORTED_LANGS:
        assert auto_opening_text(lang).endswith("Please speak in your language.")


def test_partner_auto_opening_names_only_the_listeners_language():
    assert partner_auto_opening_text("hi-IN") == "हिंदी. बोलिए."
    assert partner_auto_opening_text("en-IN") == "English. Go ahead."
    assert partner_auto_opening_text("xx-IN") == "Go ahead."


def test_language_menu_lists_the_six_digits_in_hindi_and_english():
    text = language_menu_text()
    assert text.startswith("Hindi ke liye 1")
    for i, name in enumerate(["Hindi", "Marathi", "Gujarati", "Kannada", "Tamil", "English"], 1):
        assert f"{name} {i}" in text or f"{name} ke liye {i}" in text
    assert "press" in text
