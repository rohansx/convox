import re

import pytest

from dubhash.session.prompts import line_check_text, opening_text
from dubhash.session.registry import SessionRegistry, new_call_id


def test_hindi_opening_line_names_the_other_language():
    assert opening_text("hi-IN", "kn-IN") == (
        "aap hindi mein boliye, saamne wala kannada mein sunega. chhote vaakya boliye."
    )
    assert "tamil" in opening_text("hi-IN", "ta-IN")
    assert "marathi" in opening_text("hi-IN", "mr-IN")


@pytest.mark.parametrize("lang", ["kn-IN", "ta-IN", "mr-IN"])
def test_every_supported_language_has_an_opening_and_line_check(lang):
    text = opening_text(lang, "hi-IN")
    assert text and text != opening_text("xx-IN", "hi-IN")
    assert line_check_text(lang) and line_check_text(lang) != line_check_text("xx-IN")


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
