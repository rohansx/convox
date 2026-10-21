"""Chunk guards (interp/chunker.py, L9): which STT text may be committed as a chunk, and
when the frame loop asks for a commit."""

import pytest

from dubhash.interp.chunker import chunk_text_ok, pause_commits


@pytest.mark.parametrize(
    "text",
    [
        "I am standing at the main gate,",
        "मैं गेट के बाहर खड़ा हूँ,",
        "Take one tablet twice a day,",
        "The flat is on the third floor.",
        "My OTP is 4729 and the parcel",  # a number inside is fine
    ],
)
def test_ok(text):
    assert chunk_text_ok(text)


@pytest.mark.parametrize("text", ["", "  ", "main gate ke", "OK, sure, fine."])
def test_fewer_than_4_words_waits(text):
    assert not chunk_text_ok(text)


def test_4_words_is_enough():
    assert chunk_text_ok("I reached the gate.")


@pytest.mark.parametrize(
    "text",
    [
        "My OTP is 47",  # the number may go on: 47... 29
        "My OTP is 47,",
        "मेरा ओटीपी है 47.",
        "ओटीपी 4729 आहे आणि ४७",  # Devanagari digits
        "the amount is ₹500",
        "the amount comes to three",  # number word
        "OTP is four seven two nine",
        "पिन कोड है चार सात",
        "ઓટીપી ચાર સાત બે",
        "ಓಟಿಪಿ ನಾಲ್ಕು ಏಳು ಎರಡು",
        "please come to flat",  # unclosed quantity
        "the total is ₹",
        "I live in flat number",
        "मैं फ्लैट नंबर",
        "you have to pay Rs.",
        "the parcel will come at",  # dangling English function word
        "he gave it to the",
    ],
)
def test_waits_for_more(text):
    assert not chunk_text_ok(text), text


def test_pause_commits_only_after_pause_and_min_speech():
    # pause 300, min 1200: chunk started at 0, last speech at 1500
    assert pause_commits(now=1800, t_last_speech=1500, chunk_t0=0, pause_ms=300, min_ms=1200)
    assert not pause_commits(now=1780, t_last_speech=1500, chunk_t0=0, pause_ms=300, min_ms=1200)
    # under chunk_min_ms of speech in this chunk
    assert not pause_commits(now=1800, t_last_speech=1500, chunk_t0=400, pause_ms=300, min_ms=1200)
    # the last speech frame counts (20 ms): 1180 + 20 = 1200
    assert pause_commits(now=1480, t_last_speech=1180, chunk_t0=0, pause_ms=300, min_ms=1200)
