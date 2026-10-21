"""Phrase packs and exact-after-normalisation phrase matching (interp/phrases.py)."""

from __future__ import annotations

import pytest

from dubhash.interp.phrases import PhrasePack, load_pack, normalize_phrase, verticals


def test_normalize_lowercases_and_strips_punctuation():
    assert normalize_phrase("  Theek hai!! ") == "theek hai"
    assert normalize_phrase("ठीक है।") == "ठीक है"
    assert normalize_phrase("OK, sir.") == "okay sir"


def test_normalize_keeps_a_question():
    """A cache must never return a different meaning: "haan?" is not "haan"."""
    assert normalize_phrase("Haan?") != normalize_phrase("Haan")
    assert normalize_phrase("haan ?") == normalize_phrase("Haan?")


def test_normalize_spelling_variants():
    assert normalize_phrase("thik hai") == normalize_phrase("theek hai")
    assert normalize_phrase("Okk") == normalize_phrase("okay") == normalize_phrase("OK")
    assert normalize_phrase("haa") == normalize_phrase("haan")
    assert normalize_phrase("2 mins") == normalize_phrase("2 minute")
    assert normalize_phrase("हां") == normalize_phrase("हाँ")


def test_normalize_digits():
    assert normalize_phrase("२ मिनट") == normalize_phrase("2 मिनट")
    assert normalize_phrase("૪૭૨૯") == "4729"


def test_normalize_never_merges_different_words():
    assert normalize_phrase("nahi") != normalize_phrase("haan")
    assert normalize_phrase("2 minute") != normalize_phrase("5 minute")
    assert normalize_phrase("kal subah aana") != normalize_phrase("kal shaam aana")
    # Marathi "हा" means "this", not Hindi "हाँ" (yes)
    assert normalize_phrase("हा") != normalize_phrase("हाँ")


@pytest.mark.parametrize("vertical", ["delivery", "clinic"])
def test_packs_load_with_about_forty_lines_in_four_languages(vertical):
    pack = load_pack(vertical)
    assert pack.vertical == vertical
    assert 35 <= len(pack.lines) <= 60
    for lang in ("hi", "mr", "gu", "en"):
        assert sum(1 for ln in pack.lines if ln.texts.get(lang)) >= 35, lang
    assert pack.numbers_readback
    assert any(ln.ack for ln in pack.lines)


@pytest.mark.parametrize("vertical", ["delivery", "clinic"])
def test_every_non_english_line_is_marked_for_native_review(vertical):
    pack = load_pack(vertical)
    langs = {lang for ln in pack.lines for lang in ln.texts}
    assert langs - {"en"} <= set(pack.native_review_todo)


@pytest.mark.parametrize("vertical", ["delivery", "clinic"])
def test_no_two_lines_share_a_normalised_text(vertical):
    """Otherwise a match would be ambiguous between two meanings."""
    pack = load_pack(vertical)
    for lang in ("hi", "mr", "gu", "en"):
        seen: dict[str, str] = {}
        for ln in pack.lines:
            for t in ln.texts.get(lang, ()):
                k = normalize_phrase(t)
                assert seen.setdefault(k, ln.id) == ln.id, (lang, t, ln.id, seen[k])


def test_verticals_lists_the_packs():
    assert {"delivery", "clinic"} <= set(verticals())


def test_unknown_vertical_raises():
    with pytest.raises(KeyError):
        load_pack("plumbing")


def test_match_by_language_returns_the_canonical_text():
    pack = PhrasePack.from_dict(
        {
            "vertical": "t",
            "lines": [
                {"id": "ok", "ack": True, "hi": ["ठीक है", "theek hai"], "en": ["Okay"]},
                {"id": "gate", "hi": ["मैं गेट के बाहर हूँ"], "en": ["I am outside the gate"]},
            ],
        }
    )
    m = pack.match("Thik hai.", "hi-IN")
    assert m is not None and m.line.id == "ok" and m.text == "ठीक है" and m.line.ack
    assert pack.match("okay!", "en-IN").text == "Okay"
    assert pack.match("मैं गेट के बाहर हूँ।", "hi-IN").line.id == "gate"
    assert pack.match("theek hai", "en-IN") is None  # other language's column
    assert pack.match("theek hai?", "hi-IN") is None  # a question is another meaning
    assert pack.match("main gate ke bahar hoon bhaiya", "hi-IN") is None  # no partial match
    assert pack.match("", "hi-IN") is None


def test_canonical_texts_per_language():
    pack = load_pack("delivery")
    texts = pack.texts("en-IN")
    assert texts and len(texts) == len(set(texts))
    assert pack.texts("kn-IN") == ["ಸರಿ", "ಹೌದು", "ಇಲ್ಲ"]  # acks only, acks first
    assert pack.texts("te-IN") == []
