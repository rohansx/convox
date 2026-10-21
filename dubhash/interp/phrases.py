"""Domain phrase packs (config/phrases/<vertical>.yaml) and exact phrase matching (L10).

A pack is ~40 short lines a vertical's calls repeat ("theek hai", "OTP batao", "dawai
khana khane ke baad"), each written in hi, mr, gu and en (a few acks also in kn and ta).
The first text per language is the line's canonical form; the others are spellings of the
same words ("theek hai" for "ठीक है"). Per call, the session prefills the MT and TTS
caches with the canonical texts in the language each side's STT outputs (session/prefill.py),
and the interpreter matches each STT final against the same column (interp/pipeline.py).

Matching is exact after normalize_phrase: NFC, lowercase, zero-width joiners dropped,
any script's digits to ASCII, punctuation to spaces except "?", whitespace collapsed, and
a short table of spelling variants applied word by word. Nothing semantic: a final that is
not one of the pack's texts after normalisation is not a match. A cache must never return
a different meaning, so "?" is kept (a question is not a statement), variants are only ever
two spellings of one word, and no two lines of a language may normalise alike (tested).

The pack's own non-English texts were written by us: TODO(native review), listed in
`native_review_todo`. They are source texts only; translations always come from MT.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PHRASES_DIR = Path(__file__).resolve().parent.parent / "config" / "phrases"
DEFAULT_VERTICAL = "delivery"

# Two spellings of one word -> one form. Never two different words.
VARIANTS: dict[str, str] = {
    # romanised Hindi/Hinglish
    "thik": "theek",
    "thiik": "theek",
    "teek": "theek",
    "haa": "haan",
    "haan": "haan",
    "han": "haan",
    "nahin": "nahi",
    "nai": "nahi",
    "nahee": "nahi",
    "ok": "okay",
    "okk": "okay",
    "okey": "okay",
    "okkay": "okay",
    "min": "minute",
    "mins": "minute",
    "minit": "minute",
    "minutes": "minute",
    "pls": "please",
    "plz": "please",
    # Devanagari: anusvara vs chandrabindu, a common short-i misspelling
    "हां": "हाँ",
    "ठिक": "ठीक",
    "नही": "नहीं",
    # Gujarati
    "હાં": "હા",
}
_ZW = dict.fromkeys(map(ord, "​‌‍﻿"))


def _char(c: str) -> str:
    if c == "?":
        return " ? "
    if c.isdigit():
        try:
            return str(unicodedata.digit(c))
        except ValueError:
            return c
    if unicodedata.category(c).startswith("P"):
        return " "
    return c


def normalize_phrase(text: str) -> str:
    t = unicodedata.normalize("NFC", text).translate(_ZW).lower()
    t = "".join(_char(c) for c in t)
    words = [VARIANTS.get(w, w) for w in t.split()]
    return " ".join(words)


def lang_code(lang: str) -> str:
    """The primary subtag ("hi-IN" -> "hi"): packs are keyed by it."""
    return lang.split("-")[0].lower()


@dataclass(frozen=True, slots=True)
class PhraseLine:
    id: str
    texts: dict[str, tuple[str, ...]]  # lang code -> canonical text first, then variants
    ack: bool = False  # a short acknowledgement ("haan", "ok", "theek hai")

    def canonical(self, lang: str) -> str | None:
        texts = self.texts.get(lang_code(lang))
        return texts[0] if texts else None


@dataclass(frozen=True, slots=True)
class PhraseMatch:
    line: PhraseLine
    text: str  # the line's canonical text in the matched language


@dataclass(slots=True)
class PhrasePack:
    vertical: str
    lines: list[PhraseLine]
    numbers_readback: bool = True
    native_review_todo: list[str] = field(default_factory=list)
    _index: dict[str, dict[str, PhraseLine]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for ln in self.lines:
            for lang, texts in ln.texts.items():
                idx = self._index.setdefault(lang, {})
                for t in texts:
                    key = normalize_phrase(t)
                    if key and key not in idx:
                        idx[key] = ln

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PhrasePack:
        lines: list[PhraseLine] = []
        for item in raw.get("lines") or []:
            texts: dict[str, tuple[str, ...]] = {}
            for k, v in item.items():
                if k in ("id", "ack"):
                    continue
                vals = [v] if isinstance(v, str) else list(v or [])
                vals = [str(x) for x in vals if str(x).strip()]
                if vals:
                    texts[k] = tuple(vals)
            lines.append(PhraseLine(str(item["id"]), texts, bool(item.get("ack", False))))
        return cls(
            vertical=str(raw.get("vertical", "")),
            lines=lines,
            numbers_readback=bool(raw.get("numbers_readback", True)),
            native_review_todo=list(raw.get("native_review_todo") or []),
        )

    def match(self, text: str, lang: str) -> PhraseMatch | None:
        """The line whose `lang` text equals `text` after normalisation, if any."""
        key = normalize_phrase(text)
        if not key:
            return None
        line = self._index.get(lang_code(lang), {}).get(key)
        if line is None:
            return None
        canonical = line.canonical(lang)
        assert canonical is not None
        return PhraseMatch(line, canonical)

    def texts(self, lang: str) -> list[str]:
        """Canonical texts in `lang`, acknowledgements first (the prefill order)."""
        ordered = sorted(self.lines, key=lambda ln: not ln.ack)
        out = [c for ln in ordered if (c := ln.canonical(lang)) is not None]
        return list(dict.fromkeys(out))


def verticals() -> list[str]:
    return sorted(p.stem for p in PHRASES_DIR.glob("*.yaml"))


@lru_cache
def load_pack(vertical: str) -> PhrasePack:
    path = PHRASES_DIR / f"{vertical}.yaml"
    if not path.exists():
        raise KeyError(f"no phrase pack {vertical!r}; expected one of {verticals()}")
    return PhrasePack.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")))
