"""Load config/pairs.yaml into typed PairConfig objects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import yaml

from convox.types import Side

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
    # The speaker's original under the translation, on calls that play it (telephony;
    # co-located /join and loopback calls do not): duck_db for the first duck_ms of TTS.
    duck_db: float = -20.0
    duck_ms: int = 600
    spec_mt: bool = False  # speculative MT on the latest STT partial during the EOU wait
    spec_after_ms: int = 200  # silence before speculating
    # finalise STT after this much silence, before EOU, so MT/TTS overlap the EOU wait.
    # 0 = off (interp/early_final.py, open question L3)
    early_final_ms: int = 0
    # end an utterance at the next pause once it has run this long (0 = off, L7)
    max_utterance_ms: int = 15000
    # chunked interpretation (interp/chunker.py, L9): inside an utterance, after a pause of
    # chunk_pause_ms once the chunk has chunk_min_ms of speech, translate and play that
    # chunk while the speaker goes on
    chunking: bool = False
    chunk_pause_ms: int = 300
    chunk_min_ms: int = 1200

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
            duck_db=float(cfg.get("duck_db", -20)),
            duck_ms=int(cfg.get("duck_ms", 600)),
            spec_mt=bool(cfg.get("spec_mt", False)),
            spec_after_ms=int(cfg.get("spec_after_ms", 200)),
            early_final_ms=int(cfg.get("early_final_ms", 0)),
            max_utterance_ms=int(cfg.get("max_utterance_ms", 15000)),
            chunking=bool(cfg.get("chunking", False)),
            chunk_pause_ms=int(cfg.get("chunk_pause_ms", 300)),
            chunk_min_ms=int(cfg.get("chunk_min_ms", 1200)),
        )
    return out


def orient(pair: PairConfig, lang_a: str, lang_b: str) -> PairConfig | None:
    """`pair` with side a speaking lang_a and side b lang_b: as is, swapped, or None."""
    if (pair.a.lang, pair.b.lang) == (lang_a, lang_b):
        return pair
    if (pair.b.lang, pair.a.lang) == (lang_a, lang_b):
        return pair.swapped()
    return None


AUTO = "auto"  # a side's lang when it is detected on the call (session/language.py)
AUTO_FALLBACK_LANG = "hi-IN"  # an auto side's language until detected, without a hint
PIVOT_STT, PIVOT_SRC = "sarvam-translate", "en-IN"  # speech -> English, then MT (L1)
PIVOT_EARLY_FINAL_MS = 200  # the tuned pivot pattern (L4, L6)


def default_side(lang: str, tts: str = "bulbul") -> SideConfig:
    """A side by the provider convention: Sarvam STT, `tts` voices <short>-male/female-1."""
    short = lang.split("-")[0]
    return SideConfig(
        lang=lang,
        stt="sarvam",
        tts=tts,
        voice_m=f"{tts}:{short}-male-1",
        voice_f=f"{tts}:{short}-female-1",
    )


def language_sides(pairs: dict[str, PairConfig]) -> dict[str, SideConfig]:
    """Per-language side defaults: the first non-fake pairs.yaml side for each language
    (its stt, tts and voices). Used where a side's language is not fixed by a pair entry:
    ward mode's free pick and lang "auto"."""
    known: dict[str, SideConfig] = {}
    for p in pairs.values():
        if "fake" in p.key:
            continue
        for sc in (p.a, p.b):
            known.setdefault(sc.lang, sc)
    return known


def auto_side(lang: str, sides: dict[str, SideConfig]) -> SideConfig:
    """An auto side speaking `lang`: its tts and voices from the language's defaults, its
    STT through the English pivot, so translating its speech never needs its language."""
    base = sides.get(lang) or default_side(lang)
    return replace(base, lang=lang, stt=PIVOT_STT, mt_src=PIVOT_SRC)


def effective_pair(
    base: PairConfig, langs: dict[Side, str], sides: dict[str, SideConfig]
) -> PairConfig:
    """`base` with the sides in `langs` now speaking those languages: tts and voices from
    the language's defaults; stt and mt_src stay the base side's (its STT provider is built
    once per call). Pair-level knobs (eou_ms, early_final_ms, ...) are kept."""
    out = base
    for s, lang in langs.items():
        cur = base.side(s)
        if cur.lang == lang:
            continue
        new = replace(sides.get(lang) or default_side(lang), lang=lang)
        new = replace(new, stt=cur.stt, mt_src=cur.mt_src)
        out = replace(out, a=new) if s == "a" else replace(out, b=new)
    return out


def auto_pair(
    pairs: dict[str, PairConfig],
    langs: dict[Side, str],
    hints: dict[Side, str | None],
    supported: tuple[str, ...] | None = None,
) -> PairConfig:
    """The starting pair for a call with at least one `auto` side. An auto side starts in
    its hint (else AUTO_FALLBACK_LANG) on the pivot STT; a fixed side keeps the config the
    pairs.yaml entry for the starting languages gives it, else its language's defaults.
    Pair-level knobs come from that entry, else the defaults with the pivot's
    early_final_ms. Raises ValueError for a fixed language we have no side for."""
    from convox.interp.langid import SUPPORTED_LANGS

    supported = supported or SUPPORTED_LANGS
    sides = language_sides(pairs)
    start: dict[Side, str] = {}
    for s in ("a", "b"):
        lang = langs[s]
        if lang == AUTO:
            hint = hints.get(s)
            start[s] = hint if hint in supported else AUTO_FALLBACK_LANG
        elif lang not in supported or lang not in sides:
            raise ValueError(f"unsupported language {lang!r}")
        else:
            start[s] = lang
    found = find_pair(pairs, start["a"], start["b"]) if start["a"] != start["b"] else None
    cfg: dict[Side, SideConfig] = {}
    for s in ("a", "b"):
        if langs[s] == AUTO:
            cfg[s] = auto_side(start[s], sides)
        else:
            cfg[s] = found.side(s) if found is not None else sides[start[s]]

    def short(s: Side) -> str:
        return AUTO if langs[s] == AUTO else langs[s].split("-")[0]

    key = f"{short('a')}-{short('b')}"
    if found is not None:
        return replace(found, key=key, a=cfg["a"], b=cfg["b"])
    return PairConfig(key=key, a=cfg["a"], b=cfg["b"], early_final_ms=PIVOT_EARLY_FINAL_MS)


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
