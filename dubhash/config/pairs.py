"""Load config/pairs.yaml into typed PairConfig objects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import yaml

from dubhash.types import Side

Gender = Literal["m", "f"]


@dataclass(frozen=True, slots=True)
class SideConfig:
    lang: str  # BCP-47, e.g. "hi-IN"
    stt: str
    tts: str
    voice_m: str
    voice_f: str
    # MT source language when the STT does not output `lang`, e.g. "en-IN" for an STT
    # that translates speech to English (sarvam-translate). None means `lang`.
    mt_src: str | None = None

    @property
    def mt_lang(self) -> str:
        return self.mt_src or self.lang

    def voice(self, gender: Gender) -> str:
        return self.voice_m if gender == "m" else self.voice_f


@dataclass(frozen=True, slots=True)
class PairConfig:
    key: str
    a: SideConfig
    b: SideConfig
    eou_ms: int = 550
    backchannel_max_ms: int = 600
    barge_in_min_ms: int = 1200
    duck_db: float = -14.0
    spec_mt: bool = False  # speculative MT on the latest STT partial during the EOU wait
    spec_after_ms: int = 200  # silence before speculating
    # finalise STT after this much silence, before EOU, so MT/TTS overlap the EOU wait.
    # 0 = off (interp/early_final.py, open question L3)
    early_final_ms: int = 0
    # end an utterance at the next pause once it has run this long (0 = off, L7)
    max_utterance_ms: int = 7000

    def side(self, side: Side) -> SideConfig:
        return self.a if side == "a" else self.b

    def swapped(self) -> PairConfig:
        """The same pair with the sides exchanged (a <-> b). Everything else, the key
        included, is kept: it still names the pairs.yaml entry it came from."""
        return replace(self, a=self.b, b=self.a)


def load_pairs(path: Path) -> dict[str, PairConfig]:
    raw = yaml.safe_load(path.read_text())
    out: dict[str, PairConfig] = {}
    for key, cfg in raw.items():
        out[key] = PairConfig(
            key=key,
            a=SideConfig(**cfg["a"]),
            b=SideConfig(**cfg["b"]),
            eou_ms=int(cfg.get("eou_ms", 550)),
            backchannel_max_ms=int(cfg.get("backchannel_max_ms", 600)),
            barge_in_min_ms=int(cfg.get("barge_in_min_ms", 1200)),
            duck_db=float(cfg.get("duck_db", -14)),
            spec_mt=bool(cfg.get("spec_mt", False)),
            spec_after_ms=int(cfg.get("spec_after_ms", 200)),
            early_final_ms=int(cfg.get("early_final_ms", 0)),
            max_utterance_ms=int(cfg.get("max_utterance_ms", 7000)),
        )
    return out


def orient(pair: PairConfig, lang_a: str, lang_b: str) -> PairConfig | None:
    """`pair` with side a speaking lang_a and side b lang_b: as is, swapped, or None."""
    if (pair.a.lang, pair.b.lang) == (lang_a, lang_b):
        return pair
    if (pair.b.lang, pair.a.lang) == (lang_a, lang_b):
        return pair.swapped()
    return None


def find_pair(pairs: dict[str, PairConfig], lang_a: str, lang_b: str) -> PairConfig | None:
    """The first non-fake pair for these side languages. An entry written in the exact
    orientation wins; otherwise one written the other way round is returned swapped, so
    one entry (e.g. mr-en) serves calls that put either language on side a."""
    real = [p for p in pairs.values() if "fake" not in p.key]
    for p in real:
        if (p.a.lang, p.b.lang) == (lang_a, lang_b):
            return p
    for p in real:
        if (p.b.lang, p.a.lang) == (lang_a, lang_b):
            return p.swapped()
    return None
