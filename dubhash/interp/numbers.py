"""Numbers in a turn: the ward check (extract/check) and the interpreter's read-back.

- extract_numbers / check_numbers: digit runs (any script's digits, normalised to ASCII,
  Indian and Western thousands grouping removed) compared as a multiset: every source
  number must appear in the translation. Extra numbers in the translation are fine (MT
  may turn a number word into digits). Used by ward mode and tools/live_loopback.py.
- readback (L6, L10): for verticals where numbers matter (a phrase pack's
  numbers_readback), the interpreter runs it on every translation. A source number that
  is missing from the translation replaces a translation number that is not in the source
  (in order), else it is appended after a comma, spoken in the target language: one digit
  as its word, two digits as written, three or more digit by digit ("चार सात दो नौ", how
  an OTP or a flat number is read back). A missing number that the translation has as a
  word (one digit: the target language's or the English word; longer: its digit words)
  counts as present. Number words in the SOURCE are not parsed.

What it cannot fix: a number the STT already got wrong. On the gu-hi pivot (L6) the STT's
own English final read "TP 47296" for "ઓટીપી 4729 છે", so source and translation agree.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

_NUMBER = re.compile(r"\d+(?:[.,:/]\d+)*")
_GROUPING = re.compile(r"(?<=\d),(?=\d{2,3}\b)")
_RUN = re.compile(r"\d+(?:,\d{2,3}(?!\d))*")

DIGIT_WORDS: dict[str, tuple[str, ...]] = {
    "en": ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"),
    "hi": ("शून्य", "एक", "दो", "तीन", "चार", "पांच", "छह", "सात", "आठ", "नौ"),
    "mr": ("शून्य", "एक", "दोन", "तीन", "चार", "पाच", "सहा", "सात", "आठ", "नऊ"),
    "gu": ("શૂન્ય", "એક", "બે", "ત્રણ", "ચાર", "પાંચ", "છ", "સાત", "આઠ", "નવ"),
    "kn": ("ಸೊನ್ನೆ", "ಒಂದು", "ಎರಡು", "ಮೂರು", "ನಾಲ್ಕು", "ಐದು", "ಆರು", "ಏಳು", "ಎಂಟು", "ಒಂಬತ್ತು"),
    "ta": ("பூஜ்ஜியம்", "ஒன்று", "இரண்டு", "மூன்று", "நான்கு", "ஐந்து", "ஆறு", "ஏழு", "எட்டு", "ஒன்பது"),
    "te": ("సున్నా", "ఒకటి", "రెండు", "మూడు", "నాలుగు", "ఐదు", "ఆరు", "ఏడు", "ఎనిమిది", "తొమ్మిది"),
    "bn": ("শূন্য", "এক", "দুই", "তিন", "চার", "পাঁচ", "ছয়", "সাত", "আট", "নয়"),
}  # fmt: skip
# other spellings of a digit's word that also count as "the number is there"
_ALSO = {"hi": {"5": ("पाँच",), "6": ("छः", "छे")}}


def _ascii_digits(s: str) -> str:
    return "".join(str(unicodedata.digit(c)) if c.isdigit() else c for c in s)


def extract_numbers(text: str) -> list[str]:
    """Digit runs in order, in ASCII, without leading zeros. "8:30" -> ["8", "30"],
    "1,50,000" -> ["150000"], "०८" -> ["8"]."""
    text = _GROUPING.sub("", _ascii_digits(text))
    out: list[str] = []
    for m in _NUMBER.finditer(text):
        out.extend(str(int(run)) for run in re.findall(r"\d+", m.group()))
    return out


def check_numbers(src_text: str, tgt_text: str) -> str | None:
    """None when every number in src_text appears in tgt_text, else a warning."""
    src = extract_numbers(src_text)
    missing = Counter(src) - Counter(extract_numbers(tgt_text))
    if not missing:
        return None
    listed: list[str] = []
    for n in src:  # source order, each missing occurrence once
        if missing[n] > 0:
            listed.append(n)
            missing[n] -= 1
    return f"numbers differ: {', '.join(listed)} missing in translation"


def _lang(lang: str) -> str:
    return lang.split("-")[0].lower()


def spoken(number: str, lang: str) -> str:
    """How the read-back says `number` (ASCII digits) in `lang`."""
    words = DIGIT_WORDS.get(_lang(lang))
    if len(number) == 2:
        return number
    if words is None:
        return " ".join(number)
    return " ".join(words[int(d)] for d in number)


def _words(text: str) -> list[str]:
    """Lowercase words, punctuation as spaces (Indic vowel signs stay in their word)."""
    chars = (" " if unicodedata.category(c).startswith("P") else c for c in text.lower())
    return "".join(chars).split()


def _said_as_words(number: str, tgt_words: list[str], lang: str) -> bool:
    code = _lang(lang)
    joined = f" {' '.join(tgt_words)} "
    forms: list[str] = []
    for table in (DIGIT_WORDS.get(code), DIGIT_WORDS["en"]):
        if table is not None and (len(number) == 1 or len(number) >= 3):
            forms.append(" ".join(table[int(d)] for d in number))
    if len(number) == 1:
        forms.extend(_ALSO.get(code, {}).get(number, ()))
    return any(f" {f.lower()} " in joined for f in forms)


@dataclass(slots=True)
class NumberFix:
    missing: list[str] = field(default_factory=list)  # source numbers not in the translation
    replaced: int = 0  # translation numbers replaced by one of them
    appended: int = 0  # appended after the translation


def readback(src_text: str, tgt_text: str, tgt_lang: str) -> tuple[str, NumberFix | None]:
    """tgt_text with every source number present (see the module doc), and what changed."""
    src = extract_numbers(src_text)
    if not src:
        return tgt_text, None
    runs = [(m.start(), m.end(), str(int(m.group().replace(",", ""))))
            for m in _RUN.finditer(_ascii_digits(tgt_text))]  # fmt: skip
    have = Counter(v for _, _, v in runs)
    missing_c = Counter(src) - have
    if not missing_c:
        return tgt_text, None
    tgt_words = _words(tgt_text)
    missing: list[str] = []
    for n in src:  # source order
        if missing_c[n] > 0:
            missing_c[n] -= 1
            if not _said_as_words(n, tgt_words, tgt_lang):
                missing.append(n)
    if not missing:
        return tgt_text, None
    extra = Counter(v for _, _, v in runs) - Counter(src)
    fix = NumberFix(missing=list(missing))
    todo = list(missing)
    out: list[str] = []
    pos = 0
    for start, end, v in runs:
        if todo and extra[v] > 0:
            extra[v] -= 1
            out.append(tgt_text[pos:start])
            out.append(spoken(todo.pop(0), tgt_lang))
            pos = end
            fix.replaced += 1
    out.append(tgt_text[pos:])
    text = "".join(out)
    if todo:
        text = text.rstrip()
        fix.appended = len(todo)
        text = f"{text}, {', '.join(spoken(n, tgt_lang) for n in todo)}"
    return text, fix
