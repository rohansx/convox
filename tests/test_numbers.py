"""Number read-back (interp/numbers.py): a number in the source turn must be in the
translation; a wrong or missing one is replaced by the source digits, spoken."""

from __future__ import annotations

from dubhash.interp.numbers import check_numbers, extract_numbers, readback, spoken
from dubhash.ward import service


def test_ward_reuses_the_same_number_check():
    assert service.check_numbers is check_numbers
    assert service.extract_numbers is extract_numbers


def test_unchanged_when_every_number_survives():
    assert readback("flat 302, OTP 4729", "फ्लैट 302, ओटीपी 4729", "hi-IN") == (
        "फ्लैट 302, ओटीपी 4729",
        None,
    )
    assert readback("at 8:30", "8:30 बजे", "hi-IN")[1] is None
    assert readback("no numbers here", "यहाँ कोई नंबर नहीं", "hi-IN")[1] is None


def test_native_digits_count_as_the_same_number():
    assert readback("OTP ४७२९", "OTP 4729", "en-IN")[1] is None
    assert readback("OTP 4729", "ઓટીપી ૪૭૨૯", "gu-IN")[1] is None


def test_a_wrong_number_is_replaced_by_the_source_digits_spoken():
    """The L6 shape: 4729 came back as 47296."""
    text, fix = readback("OTP is 4729.", "OTP 47296 है।", "hi-IN")
    assert text == "OTP चार सात दो नौ है।"
    assert fix is not None and fix.missing == ["4729"] and fix.replaced == 1 and not fix.appended


def test_replacement_in_gujarati_and_marathi():
    assert readback("OTP 4729", "ઓટીપી 47296 છે", "gu-IN")[0] == "ઓટીપી ચાર સાત બે નવ છે"
    assert readback("flat 302", "फ्लॅट 320", "mr-IN")[0] == "फ्लॅट तीन शून्य दोन"


def test_a_dropped_number_is_appended():
    text, fix = readback("OTP is 4729", "ओटीपी बता दिया", "hi-IN")
    assert text == "ओटीपी बता दिया, चार सात दो नौ"
    assert fix is not None and fix.appended == 1


def test_a_number_written_as_a_word_is_accepted():
    """MT may write 2 as "दो" or "two": same meaning, no fix."""
    assert readback("2 minutes", "दो मिनट", "hi-IN")[1] is None
    assert readback("at 6 pm", "शाम six बजे", "hi-IN")[1] is None
    assert readback("2 minute", "બે મિનિટ", "gu-IN")[1] is None


def test_extra_numbers_in_the_translation_are_left_alone():
    assert readback("two minutes", "2 मिनट", "hi-IN")[1] is None


def test_several_numbers_pair_in_order():
    text, fix = readback("flat 302 OTP 4729", "flat 203 OTP 4729", "en-IN")
    assert text == "flat three zero two OTP 4729"
    assert fix is not None and fix.missing == ["302"]


def test_spoken_forms():
    assert spoken("6", "hi-IN") == "छह"
    assert spoken("15", "hi-IN") == "15"  # two digits: the number as written
    assert spoken("4729", "kn-IN") == "ನಾಲ್ಕು ಏಳು ಎರಡು ಒಂಬತ್ತು"
    assert spoken("4729", "xx-IN") == "4 7 2 9"  # no table: digits one by one
