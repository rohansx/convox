import re

import pytest

from dubhash.session.prompts import line_check_text, opening_text
from dubhash.session.registry import SessionRegistry, new_call_id


def test_hindi_opening_line_names_the_other_language():
    assert opening_text("hi-IN", "kn-IN") == (
        "aap hindi mein chhote vaakya boliye, woh kannada mein sunenge."
    )
    assert "tamil" in opening_text("hi-IN", "ta-IN")
    assert "marathi" in opening_text("hi-IN", "mr-IN")
    assert "angrezi" in opening_text("hi-IN", "en-IN")


@pytest.mark.parametrize("lang", ["kn-IN", "ta-IN", "mr-IN", "gu-IN"])
def test_every_supported_language_has_an_opening_and_line_check(lang):
    text = opening_text(lang, "hi-IN")
    assert text and text != opening_text("xx-IN", "hi-IN")
    assert line_check_text(lang) and line_check_text(lang) != line_check_text("xx-IN")


LANGS = ["hi-IN", "kn-IN", "ta-IN", "mr-IN", "gu-IN", "en-IN"]


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("other", LANGS)
def test_opening_line_is_one_short_sentence(lang, other):
    """About 3-4 s of Bulbul speech (L4: 3.2-4.3 s live; it was 7-9 s): one sentence, at
    most ~70 code points."""
    if lang == other:
        return
    text = opening_text(lang, other)
    assert len(re.findall(r"[.?!।]", text.rstrip(".?!।"))) == 0, text  # one sentence
    assert len(text) <= 72, (len(text), text)


def test_marathi_and_english_openings_for_the_marathi_pairs():
    assert opening_text("mr-IN", "en-IN") == ("मराठीत छोटी वाक्ये बोला, ते इंग्रजीत ऐकतील.")
    assert opening_text("mr-IN", "hi-IN") == "मराठीत छोटी वाक्ये बोला, ते हिंदीत ऐकतील."
    assert opening_text("en-IN", "mr-IN") == ("Speak short English sentences; they hear Marathi.")
    assert line_check_text("en-IN") == "Are you still on the line?"
    assert line_check_text("mr-IN") == "तुम्ही लाइनवर आहात का?"


def test_gujarati_openings_for_the_gujarati_pairs():
    assert opening_text("gu-IN", "hi-IN") == "ગુજરાતીમાં નાનાં વાક્યો બોલો, તેઓ હિન્દીમાં સાંભળશે."
    assert opening_text("gu-IN", "en-IN") == "ગુજરાતીમાં નાનાં વાક્યો બોલો, તેઓ અંગ્રેજીમાં સાંભળશે."
    assert opening_text("hi-IN", "gu-IN") == (
        "aap hindi mein chhote vaakya boliye, woh gujarati mein sunenge."
    )
    assert opening_text("en-IN", "gu-IN") == "Speak short English sentences; they hear Gujarati."
    assert line_check_text("gu-IN") == "તમે લાઇન પર છો?"
    assert opening_text("gu-IN", "hi-IN", record=True) != opening_text("gu-IN", "hi-IN")


def test_kannada_opening_matches_the_spec_sentence():
    assert opening_text("kn-IN", "hi-IN").startswith("ನೀವು ಕನ್ನಡದಲ್ಲಿ ಮಾತನಾಡಿ, ಅವರು ಹಿಂದಿಯಲ್ಲಿ ಕೇಳುತ್ತಾರೆ.")


def test_line_check_hindi_and_unknown_fallback():
    assert line_check_text("hi-IN") == "aap line par hain?"
    assert line_check_text("en-IN")  # english fallback, never empty


def test_record_adds_a_text_saved_sentence():
    plain = opening_text("hi-IN", "kn-IN")
    rec = opening_text("hi-IN", "kn-IN", record=True)
    assert rec.startswith(plain) and len(rec) > len(plain)


def test_call_ids_are_c_plus_10_lowercase_base32():
    ids = {new_call_id() for _ in range(200)}
    assert len(ids) == 200
    for cid in ids:
        assert re.fullmatch(r"c_[a-z2-7]{10}", cid)


class _S:
    def __init__(self, name):
        self.name = name


def test_registry_create_get_list_remove():
    reg = SessionRegistry()
    s1, s2 = _S(1), _S(2)
    reg.create("c_1", s1)  # type: ignore[arg-type]
    reg.create("c_2", s2)  # type: ignore[arg-type]
    assert reg.get("c_1") is s1 and reg.get("nope") is None
    assert reg.list() == [s1, s2]
    with pytest.raises(KeyError):
        reg.create("c_1", s2)  # type: ignore[arg-type]
    reg.remove("c_1")
    reg.remove("c_1")  # idempotent
    assert reg.list() == [s2] and len(reg) == 1 and "c_2" in reg
