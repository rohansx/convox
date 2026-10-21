"""Chunked (clause-level) interpretation, L9: the rules that decide when part of an
utterance is translated and played while the speaker goes on. The pipeline
(interp/pipeline.py) does the work; this module holds the decisions, as pure functions.

A chunk is cut at a pause inside an utterance, using the early-final machinery: once
silence has lasted the cut threshold, the STT stream is ended. When the pause reaches
chunk_pause_ms and the chunk so far has at least chunk_min_ms of speech
(pause_commits), the pipeline asks for a commit. The STT final of the chunk must then
pass chunk_text_ok, else the chunk is not committed and, if speech resumes, the stream is
restarted on the whole buffered chunk as an early final would be (nothing is lost; the
next pause tries again with the longer text).

chunk_text_ok refuses a text that:
- has fewer than MIN_WORDS (4) words: too little context to translate on its own;
- ends with a digit or a number word ("OTP 47", "चार सात"): the number may go on;
- ends with an unclosed quantity ("flat", "OTP", "number", "₹", "Rs."): its number is
  still to come;
- ends with a dangling English function word ("at", "the", "to"): the pivot's English
  is mid-phrase.
Number and quantity words cover English (the pivot STT's output) and hi/mr/gu/kn.
"""

from __future__ import annotations

import re

MIN_WORDS = 4
FRAME_MS = 20

_STRIP = " \t\n.,!?;:।॥\"'()[]{}…-–—"
_NUMBER_WORDS = frozenset(
    """
    zero oh one two three four five six seven eight nine ten eleven twelve thirteen
    fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty
    seventy eighty ninety hundred thousand lakh lakhs crore crores million double triple
    शून्य एक दो तीन चार पांच पाँच छह छः छे सात आठ नौ दस ग्यारह बारह बीस तीस चालीस पचास
    सौ हज़ार हजार लाख करोड़ करोड
    दोन पाच सहा नऊ दहा वीस शंभर कोटी
    શૂન્ય એક બે ત્રણ ચાર પાંચ છ સાત આઠ નવ દસ વીસ સો હજાર લાખ કરોડ
    ಸೊನ್ನೆ ಒಂದು ಎರಡು ಮೂರು ನಾಲ್ಕು ಐದು ಆರು ಏಳು ಎಂಟು ಒಂಬತ್ತು ಹತ್ತು ನೂರು ಸಾವಿರ ಲಕ್ಷ ಕೋಟಿ
    """.split()
)
_QUANTITY_WORDS = frozenset(
    """
    otp flat no no. number # ₹ rs rs. inr pin code plot
    ओटीपी फ्लैट फ्लॅट नंबर नम्बर क्रमांक पिन कोड
    ઓટીપી ફ્લેટ નંબર પિન કોડ
    ಓಟಿಪಿ ಫ್ಲಾಟ್ ನಂಬರ್ ಪಿನ್ ಕೋಡ್
    """.split()
)
_DANGLING = frozenset(
    "a an the of to at and or but by for with from in on is are was my your his her our".split()
)
_WORD = re.compile(r"\S+")


def _words(text: str) -> list[str]:
    return [w for w in (t.strip(_STRIP) for t in _WORD.findall(text)) if w]


def chunk_text_ok(text: str) -> bool:
    """May this STT final be committed as a chunk now (else wait for more speech)?"""
    raw = text.strip()
    if raw.endswith("₹"):
        return False
    words = _words(raw)
    if len(words) < MIN_WORDS:
        return False
    last = words[-1].lower()
    if any(ch.isdigit() for ch in last[-1:]) or last in _NUMBER_WORDS:
        return False
    if last in _QUANTITY_WORDS or last.rstrip(".") in _QUANTITY_WORDS:
        return False
    return last not in _DANGLING


def pause_commits(
    *, now: int, t_last_speech: int, chunk_t0: int, pause_ms: int, min_ms: int
) -> bool:
    """The frame loop's test: the pause has reached pause_ms and the chunk (from chunk_t0,
    the utterance start or the first speech after the last committed chunk) has min_ms of
    speech, its last frame included."""
    if now - t_last_speech < pause_ms:
        return False
    return t_last_speech - chunk_t0 + FRAME_MS >= min_ms
