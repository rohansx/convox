"""Per-side language resolution for `lang: "auto"` (docs/tech-spec/language-detection.md).

One LanguageResolver per auto side, a small state machine fed once per utterance with
the evidence the session has: the STT final's language code (when the vendor reports
one), the LanguageID guess (interp/langid.py) and the side's hint (a prior, e.g. the
platform's profile language).

    UNKNOWN ──confident guess──> PROVISIONAL ──agreeing guess──> CONFIRMED
       └──────────very confident guess (>= 0.9)───────────────────────┘

- Only utterances of at least min_speech_ms of speech are decided on.
- Evidence per language is combined as a noisy-or; the winner's confidence is discounted
  by the runner-up's, so STT and LID disagreeing is low confidence. The hint only lifts a
  language some evidence already named; on its own it never resolves a side.
- A CONFIRMED side switches only after switch_after consecutive confident utterances in
  one new language. Code-mixed English does not count: when a side is in an Indic
  language and the utterance's transcript still has MIXED_MIN_SHARE of its letters in an
  Indic script, an English guess is dropped (loanwords: "मेरा order कहाँ है"). Latin-script
  words never count as English on their own.
- A language outside SUPPORTED_LANGS is never claimed. menu_after consecutive utterances
  that are unsupported or under `confident`, on a side not yet CONFIRMED, ask once per
  call for the keypad menu (LangUpdate.menu). A keypad choice confirms and pins the side.
- Until a side resolves, translations INTO it use its hint, else FALLBACK_LANG (Hindi).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from dubhash.interp.langid import SUPPORTED_LANGS, LangGuess, normalise
from dubhash.types import Side

if TYPE_CHECKING:
    from dubhash.config.pairs import SideConfig

FALLBACK_LANG = "hi-IN"
ENGLISH = "en-IN"
MIXED_MIN_SHARE = 0.2  # Indic-script share of a transcript's letters that marks code-mixing
# The keypad menu (prompts.language_menu_text speaks it in this order).
MENU_DIGITS: dict[str, str] = {
    "1": "hi-IN",
    "2": "mr-IN",
    "3": "gu-IN",
    "4": "kn-IN",
    "5": "ta-IN",
    "6": "en-IN",
}


class LangState(StrEnum):
    UNKNOWN = "UNKNOWN"
    PROVISIONAL = "PROVISIONAL"
    CONFIRMED = "CONFIRMED"


@dataclass(frozen=True, slots=True)
class ResolverConfig:
    min_speech_ms: int = 2000  # shorter utterances are not decided on (L8: 2.0 s, never 1.0)
    confident: float = 0.6  # a combined guess at least this sure counts
    very_confident: float = 0.9  # confirms on its own
    confirm_after: int = 2  # consecutive agreeing confident utterances to confirm
    switch_after: int = 2  # consecutive confident utterances to move a confirmed side
    menu_after: int = 2  # consecutive unsure/unsupported utterances before the menu
    # weight of an STT final's language code, and the cap on its own score: Sarvam realtime
    # (language_code=auto) is wrong at 1.0 (L8), so alone it is at most PROVISIONAL
    stt_confidence: float = 0.7
    hint_prior: float = 0.15  # lift for a language that matches the hint
    supported: tuple[str, ...] = SUPPORTED_LANGS
    # languages that need more than `confident`: loanword-only Indic utterances ("Flat
    # number 302") come back en-IN at up to 0.93 (L8, providers/sarvam_lid.py)
    lang_confident: dict[str, float] = field(default_factory=lambda: {"en-IN": 0.95})


# How an auto side's STT stream opens. "guess" (default): in the side's best language so
# far (its resolved language, else its hint, else Hindi), reopened when that changes;
# translate-mode English came out the same whichever Indic code the socket opened with
# (7/7 languages, 21 Oct). "auto": Sarvam realtime's language_code=auto, whose finals carry
# the detected language and a confidence, in translate mode too ("unknown" is rejected);
# L8 found it en-IN on every short clip and wrong at 1.0, so it only adds evidence.
SttOpen = Literal["guess", "auto"]
STT_AUTO = "auto"
MENU_WINDOW_MS = 15000  # after the menu finishes playing, digits mean language this long


@dataclass(slots=True)
class AutoLang:
    """What a CallSession needs for its auto sides."""

    hints: dict[Side, str | None]  # the sides whose lang is "auto", with their hints
    sides: dict[str, SideConfig]  # per-language side defaults (config.pairs.language_sides)
    config: ResolverConfig = field(default_factory=lambda: ResolverConfig())
    stt_open: SttOpen = "guess"
    menu_window_ms: int = MENU_WINDOW_MS


@dataclass(frozen=True, slots=True)
class LangUpdate:
    """A change worth telling the call about: published as the CallEvent kind "lang"."""

    side: Side
    lang: str | None
    state: LangState
    confidence: float
    source: str  # "stt_final" | "lid" | "hint" | "dtmf"
    menu: bool = False  # the side needs the keypad menu

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "side": self.side,
            "lang": self.lang,
            "state": self.state.value,
            "confidence": round(self.confidence, 3),
            "source": self.source,
        }
        if self.menu:
            out["menu"] = True
        return out


def indic_share(text: str | None) -> float:
    """Share of `text`'s letters in an Indic script (0 without letters)."""
    letters = [c for c in text or "" if c.isalpha()]
    if not letters:
        return 0.0
    indic = sum(0x0900 <= ord(c) <= 0x0DFF for c in letters)
    return indic / len(letters)


def combine(
    *,
    stt_lang: str | None,
    lid: LangGuess | None,
    text: str | None = None,
    current: str | None = None,
    hint: str | None = None,
    config: ResolverConfig | None = None,
    stt_confidence: float | None = None,
) -> tuple[str | None, float, str, bool]:
    """(lang, confidence, source, unsupported) for one utterance's evidence. lang is None
    when nothing supported was heard; `unsupported` when only unsupported languages were.
    stt_confidence: the STT final's own score for stt_lang, when the vendor gives one."""
    config = config or ResolverConfig()
    evidence: list[tuple[str, float, str]] = []
    if (code := normalise(stt_lang)) is not None:
        conf = config.stt_confidence
        if stt_confidence is not None:
            conf = min(stt_confidence, conf)
        evidence.append((code, conf, "stt_final"))
    if lid is not None and (code := normalise(lid.lang)) is not None:
        evidence.append((code, float(lid.confidence), lid.source or "lid"))
    if current is not None and current != ENGLISH and indic_share(text) >= MIXED_MIN_SHARE:
        evidence = [e for e in evidence if e[0] != ENGLISH]  # code-mixed loanwords
    en_need = config.lang_confident.get(ENGLISH, config.confident)
    if any(
        lang != ENGLISH and lang in config.supported and conf >= config.confident
        for lang, conf, _ in evidence
    ):  # Indian speech with English words is heard as English; a sure Indic guess wins.
        # The STT final's en-IN score is not calibrated (0.99 on Hindi speech, live
        # c_f4h2dho56j), so it never outvotes one; the LID's en-IN must clear en_need.
        evidence = [
            e for e in evidence if e[0] != ENGLISH or (e[2] != "stt_final" and e[1] >= en_need)
        ]
    supported = [e for e in evidence if e[0] in config.supported]
    unsupported = bool(evidence) and not supported
    if not supported:
        return None, 0.0, "lid", unsupported
    scores: dict[str, float] = {}
    best_src: dict[str, tuple[float, str]] = {}
    for lang, conf, src in supported:
        conf = min(max(conf, 0.0), 1.0)
        scores[lang] = 1 - (1 - scores.get(lang, 0.0)) * (1 - conf)
        if conf > best_src.get(lang, (-1.0, ""))[0]:
            best_src[lang] = (conf, src)
    if hint in scores:
        scores[hint] = 1 - (1 - scores[hint]) * (1 - config.hint_prior)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    lang, score = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    return lang, score * (1 - runner), best_src[lang][1], False


class LanguageResolver:
    def __init__(
        self, side: Side, hint: str | None = None, config: ResolverConfig | None = None
    ) -> None:
        self.side = side
        self.config = config or ResolverConfig()
        hint = normalise(hint)
        self.hint = hint if hint in self.config.supported else None
        self.state = LangState.UNKNOWN
        self.lang: str | None = None
        self.confidence = 0.0
        self.source = "hint" if self.hint else "lid"
        self.menu_requested = False
        self.pinned = False  # set by a keypad choice: detection no longer moves the side
        self._agree = 0  # PROVISIONAL: consecutive confident utterances in self.lang
        self._switch: tuple[str, int] | None = None  # CONFIRMED: (new lang, consecutive)
        self._unsure = 0  # consecutive unsure/unsupported utterances

    @property
    def target(self) -> str:
        """The language to translate into for this side now."""
        return self.lang or self.hint or FALLBACK_LANG

    @property
    def resolved(self) -> bool:
        return self.state == LangState.CONFIRMED

    def _update(self, lang: str, state: LangState, conf: float, source: str) -> LangUpdate:
        self.lang, self.state, self.confidence, self.source = lang, state, conf, source
        return LangUpdate(self.side, lang, state, conf, source)

    def observe(
        self,
        *,
        speech_ms: int,
        stt_lang: str | None = None,
        lid: LangGuess | None = None,
        text: str | None = None,
        stt_confidence: float | None = None,
    ) -> LangUpdate | None:
        """One utterance's evidence. Returns the update when the side's language or state
        changed, or when the keypad menu is wanted; else None."""
        cfg = self.config
        if self.pinned or speech_ms < cfg.min_speech_ms:
            return None
        lang, conf, source, _ = combine(
            stt_lang=stt_lang,
            lid=lid,
            text=text,
            current=self.lang,
            hint=self.hint,
            config=cfg,
            stt_confidence=stt_confidence,
        )
        need = max(cfg.confident, cfg.lang_confident.get(lang or "", 0.0))
        sure = lang is not None and conf >= need
        if self.state == LangState.CONFIRMED:
            if not sure or lang == self.lang:
                self._switch = None
                return None
            assert lang is not None
            n = self._switch[1] + 1 if self._switch and self._switch[0] == lang else 1
            self._switch = (lang, n)
            if n < cfg.switch_after:
                return None
            self._switch = None
            return self._update(lang, LangState.CONFIRMED, conf, source)
        if not sure:
            self._unsure += 1
            if self._unsure >= cfg.menu_after and not self.menu_requested:
                self.menu_requested = True
                return LangUpdate(
                    self.side, self.lang, self.state, self.confidence, self.source, menu=True
                )
            return None
        assert lang is not None
        self._unsure = 0
        if conf >= cfg.very_confident:
            return self._update(lang, LangState.CONFIRMED, conf, source)
        if self.state == LangState.PROVISIONAL and lang == self.lang:
            self._agree += 1
            if self._agree >= cfg.confirm_after:
                return self._update(lang, LangState.CONFIRMED, conf, source)
            self.confidence = conf
            return None
        self._agree = 1
        return self._update(lang, LangState.PROVISIONAL, conf, source)

    def choose(self, lang: str, source: str = "dtmf") -> LangUpdate:
        """The caller picked their language (keypad menu): CONFIRMED and pinned."""
        code = normalise(lang)
        if code not in self.config.supported:
            raise ValueError(f"unsupported language {lang!r}")
        self.pinned = True
        self._switch = None
        return self._update(code, LangState.CONFIRMED, 1.0, source)
