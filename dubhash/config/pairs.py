"""Load config/pairs.yaml into typed PairConfig objects."""

from __future__ import annotations

from dataclasses import dataclass
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

    def side(self, side: Side) -> SideConfig:
        return self.a if side == "a" else self.b


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
        )
    return out


def find_pair(pairs: dict[str, PairConfig], lang_a: str, lang_b: str) -> PairConfig | None:
    """Match by side languages (either orientation is a different pair; we don't swap)."""
    for p in pairs.values():
        if p.a.lang == lang_a and p.b.lang == lang_b and "fake" not in p.key:
            return p
    return None
