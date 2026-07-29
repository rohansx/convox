"""Text normalisers used by slot and transcript assertions.

Comparing what the caller said against what the agent captured is only meaningful
after normalisation: ``+91 98765 43210``, ``9876543210``, and ``nine eight seven
six five four three two one zero`` are the same phone number, and an assertion
that says otherwise is noise.

Indic scripts need more than casefolding — matra and nukta forms, zero-width
joiners, and numeral variants all produce spurious mismatches. Those normalisers
land with the audio path; the hooks are here so the interface does not change.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")

_NUMBER_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    # Hindi/Urdu digits as commonly spoken in Indian English calls.
    "shunya": "0", "ek": "1", "do": "2", "teen": "3", "char": "4",
    "paanch": "5", "chhe": "6", "saat": "7", "aath": "8", "nau": "9",
}

_FILLERS = {"um", "uh", "erm", "hmm", "mm", "haan", "achha"}


def basic(text: str) -> str:
    """Casefold, strip punctuation and accents, collapse whitespace."""
    text = unicodedata.normalize("NFC", str(text))
    text = _PUNCT.sub(" ", text)
    text = _SPACE.sub(" ", text)
    return text.strip().casefold()


def without_fillers(text: str) -> str:
    return " ".join(w for w in basic(text).split() if w not in _FILLERS)


def digits(text: str) -> str:
    """Extract digits, mapping spoken number words to their digit."""
    out: list[str] = []
    for token in basic(text).split():
        if token.isdigit():
            out.append(token)
        elif token in _NUMBER_WORDS:
            out.append(_NUMBER_WORDS[token])
        else:
            out.extend(c for c in token if c.isdigit())
    return "".join(out)


def e164(text: str) -> str:
    """Normalise a phone number to comparable digits.

    Country codes are dropped down to the national number so that ``+919876543210``
    and ``9876543210`` compare equal — an agent that captured the number correctly
    should not fail because it stored it without the prefix.
    """
    d = digits(text)
    for prefix in ("91", "1", "44"):
        if len(d) > 10 and d.startswith(prefix):
            d = d[len(prefix):]
            break
    return d


def date(text: str) -> str:
    """Reduce a date to ``YYYYMMDD`` when the shape is recognisable."""
    d = digits(text)
    if len(d) == 8:
        return d
    return basic(text)


def time_of_day(text: str) -> str:
    d = digits(text)
    lowered = basic(text)
    if not d:
        return lowered
    hour = int(d[:2]) if len(d) >= 3 else int(d)
    minute = int(d[-2:]) if len(d) >= 3 else 0
    if "pm" in lowered and hour < 12:
        hour += 12
    if "am" in lowered and hour == 12:
        hour = 0
    return f"{hour:02d}{minute:02d}"


def currency(text: str) -> str:
    return digits(text)


def email(text: str) -> str:
    return basic(text).replace(" at ", "@").replace(" dot ", ".").replace(" ", "")


def name(text: str) -> str:
    return without_fillers(text)


NORMALIZERS = {
    "default": basic,
    "basic": basic,
    "digits": digits,
    "e164": e164,
    "date": date,
    "time": time_of_day,
    "currency": currency,
    "email": email,
    "name": name,
}


def get(kind: str | None) -> Callable[[str], str]:
    normalizer = NORMALIZERS.get(kind or "default")
    if normalizer is None:
        raise KeyError(f"unknown normalizer {kind!r} (available: {', '.join(sorted(NORMALIZERS))})")
    return normalizer


def contains(haystack: str, needle: str, kind: str | None = "default") -> bool:
    normalize = get(kind)
    return normalize(needle) in normalize(haystack)
