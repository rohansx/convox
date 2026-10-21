"""Ward mode: one phone, push-to-talk, no telephony.

A press of a side's button is one utterance, so there is no VAD or endpointing: the
press streams frames into STT and the release ends it. Then MT (cached) and TTS in the
other side's language, with a voice picked by the speaker's detected gender. Every turn
is checked for numbers that went missing in translation (entity read-back,
docs/tech-spec/security-privacy.md) and kept in an in-memory transcript the nurse can
export. Nothing here is persisted.

Decisions where the spec is open:
- Gender is detected per side from the first utterance with voiced audio, then kept, so
  one short or noisy press does not flip the voice mid-conversation.
- An empty transcript is returned as a WardTurn with empty texts and no audio, and is
  not added to the transcript or given a seq.
- The number check compares digit runs (any script's digits, normalised to ASCII, Indian
  and Western thousands grouping removed) as a multiset: every source number must appear
  in the translation. Extra numbers in the translation are not flagged (MT may turn a
  number word into digits). Number words are not parsed.
- MT translates from the side's mt_lang: with a pivot side (stt sarvam-translate, mt_src
  en-IN) the transcript is English. When that already is the target language, MT is
  skipped (mt_ms = 0).
"""

from __future__ import annotations

import re
import secrets
import time
import unicodedata
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from dubhash.audio.frames import SAMPLE_RATE, SAMPLE_WIDTH, Reframer
from dubhash.clock import Clock, MonotonicClock
from dubhash.config.pairs import (
    PairConfig,
    SideConfig,
    default_side,
    find_pair,
    language_sides,
    load_pairs,
)
from dubhash.config.settings import PACKAGE_DIR
from dubhash.interp.contracts import MT, STT, TTS, STTStream
from dubhash.interp.gender import THRESHOLD_HZ, estimate_f0
from dubhash.interp.mt_cache import CachedMT
from dubhash.types import Side, other

if TYPE_CHECKING:
    from dubhash.config.settings import Settings

Gender = Literal["m", "f"]

# Quick choices on the ward page (side A, side B); the first is the default. A Marathi
# speaker with a Hindi or an English speaker (L4), then the same for Gujarati (L6).
PRESETS: tuple[tuple[str, str], ...] = (
    ("mr-IN", "hi-IN"),
    ("mr-IN", "en-IN"),
    ("gu-IN", "hi-IN"),
    ("gu-IN", "en-IN"),
)
DEFAULT_LANGS: tuple[str, str] = PRESETS[0]
MAX_UTTERANCE_S = 60  # a press longer than this is truncated (stuck button, pocket)
GENDER_DEFAULT: Gender = "m"

# Languages offered in the picker: code -> (English name, native name).
LANGS: dict[str, tuple[str, str]] = {
    "hi-IN": ("Hindi", "हिन्दी"),
    "mr-IN": ("Marathi", "मराठी"),
    "kn-IN": ("Kannada", "ಕನ್ನಡ"),
    "ta-IN": ("Tamil", "தமிழ்"),
    "te-IN": ("Telugu", "తెలుగు"),
    "ml-IN": ("Malayalam", "മലയാളം"),
    "bn-IN": ("Bengali", "বাংলা"),
    "gu-IN": ("Gujarati", "ગુજરાતી"),
    "pa-IN": ("Punjabi", "ਪੰਜਾਬੀ"),
    "od-IN": ("Odia", "ଓଡ଼ିଆ"),
    "en-IN": ("English", "English"),
}
ELEVEN_LANGS: frozenset[str] = frozenset()  # Bulbul speaks every language (21 Oct decision)


def lang_label(code: str) -> str:
    return LANGS.get(code, (code, code))[0]


# --- pair resolution -----------------------------------------------------------------


def _default_side(lang: str) -> SideConfig:
    short = lang.split("-")[0]
    return default_side(lang, tts="eleven" if short in ELEVEN_LANGS else "bulbul")


def ward_pair(lang_a: str, lang_b: str, pairs: dict[str, PairConfig] | None = None) -> PairConfig:
    """A PairConfig for two picker languages. Ward mode lets the nurse pick any two, so
    each side's config (stt, tts, voices) comes from the pairs.yaml entry for the two
    languages (either orientation, find_pair), else from the first non-fake pair that
    speaks that language, else from the provider convention."""
    for lang in (lang_a, lang_b):
        if lang not in LANGS:
            raise ValueError(f"unsupported language {lang!r}")
    if lang_a == lang_b:
        raise ValueError("both sides have the same language")
    if pairs is None:
        pairs = load_pairs(PACKAGE_DIR / "config" / "pairs.yaml")
    exact = find_pair(pairs, lang_a, lang_b)
    if exact is not None:
        return exact
    known = language_sides(pairs)

    def side(lang: str) -> SideConfig:
        return known.get(lang) or _default_side(lang)

    key = f"{lang_a.split('-')[0]}-{lang_b.split('-')[0]}"
    return PairConfig(key=key, a=side(lang_a), b=side(lang_b))


@dataclass(slots=True)
class WardProviders:
    """stt keyed by the speaking side; tts keyed by the side whose language it speaks
    (the TARGET), as in session.providers.Providers."""

    stt: dict[Side, STT]
    mt: MT
    tts: dict[Side, TTS]


def build_ward_providers(pair: PairConfig, settings: Settings) -> WardProviders:
    from dubhash.providers.registry import build_mt, build_stt, build_tts

    return WardProviders(
        stt={s: build_stt(pair.side(s).stt, settings) for s in ("a", "b")},
        mt=build_mt("sarvam", settings),
        tts={s: build_tts(pair.side(s).tts, settings) for s in ("a", "b")},
    )


# --- numbers -------------------------------------------------------------------------

_NUMBER = re.compile(r"\d+(?:[.,:/]\d+)*")
_GROUPING = re.compile(r"(?<=\d),(?=\d{2,3}\b)")


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


# --- session -------------------------------------------------------------------------


@dataclass(slots=True)
class WardTurn:
    side: Side
    src_text: str
    tgt_text: str
    audio: bytes = b""  # PCM16 mono @16k
    timings: dict[str, int] = field(default_factory=dict)  # stage durations in ms
    warning: str | None = None
    numbers: list[str] = field(default_factory=list)  # numbers found in src_text
    seq: int = -1
    src_lang: str = ""
    tgt_lang: str = ""
    voice: str = ""
    at: str = ""  # wall-clock ISO time, for the exported transcript

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "side": self.side,
            "at": self.at,
            "src_lang": self.src_lang,
            "tgt_lang": self.tgt_lang,
            "src_text": self.src_text,
            "tgt_text": self.tgt_text,
            "warning": self.warning,
            "numbers": list(self.numbers),
            "voice": self.voice,
            "timings": dict(self.timings),
        }


def new_session_id() -> str:
    return "w_" + secrets.token_hex(5)


class WardSession:
    def __init__(
        self,
        pair_or_langs: PairConfig | tuple[str, str],
        providers: WardProviders,
        clock: Clock | None = None,
        *,
        session_id: str | None = None,
    ) -> None:
        if isinstance(pair_or_langs, PairConfig):
            self.pair = pair_or_langs
        else:
            self.pair = ward_pair(*pair_or_langs)
        self.providers = providers
        self.mt = providers.mt if isinstance(providers.mt, CachedMT) else CachedMT(providers.mt)
        self.clock: Clock = clock or MonotonicClock()
        self.session_id = session_id or new_session_id()
        self.started_at = _now_iso()
        self.transcript: list[WardTurn] = []
        self.gender: dict[Side, Gender] = {}

    @property
    def langs(self) -> dict[Side, str]:
        return {"a": self.pair.a.lang, "b": self.pair.b.lang}

    async def start(self, side: Side) -> WardUtterance:
        """Open STT for one press. Push frames while the button is held."""
        stream = await self.providers.stt[side].open(self.pair.side(side).lang)
        return WardUtterance(self, side, stream)

    async def handle_utterance(self, side: Side, pcm16: bytes) -> WardTurn:
        utt = await self.start(side)
        reframer = Reframer()
        try:
            for frame in reframer.push(pcm16):
                await utt.push(frame)
            rest = reframer.flush(pad=False)
            if rest:
                await utt.push(rest)
        except BaseException:
            await utt.cancel()
            raise
        return await utt.finish()

    def _voice(self, side: Side, audio: bytes) -> str:
        gender = self.gender.get(side)
        if gender is None:
            f0 = estimate_f0(audio)
            if f0 is not None:
                gender = self.gender[side] = "m" if f0 < THRESHOLD_HZ else "f"
            else:
                gender = GENDER_DEFAULT
        return self.pair.side(other(side)).voice(gender)

    def _record(self, turn: WardTurn) -> None:
        turn.seq = len(self.transcript)
        self.transcript.append(turn)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "pair": self.langs,
            "turns": [t.to_dict() for t in self.transcript],
        }

    def to_text(self) -> str:
        a, b = self.pair.a.lang, self.pair.b.lang
        lines = [
            "dubhash ward transcript",
            f"Session {self.session_id}, started {self.started_at}",
            f"Side A: {lang_label(a)} ({a})   Side B: {lang_label(b)} ({b})",
            "Machine translation. Check numbers and doses with the patient.",
            "",
        ]
        for t in self.transcript:
            lines.append(f"[{t.seq + 1}] {t.at}  Side {t.side.upper()}")
            lines.append(f"  {lang_label(t.src_lang)}: {t.src_text}")
            lines.append(f"  {lang_label(t.tgt_lang)}: {t.tgt_text}")
            if t.warning:
                lines.append(f"  !! {t.warning}")
            lines.append("")
        return "\n".join(lines)


class WardUtterance:
    """One press: frames go to STT as they arrive; finish() runs MT and TTS."""

    def __init__(self, session: WardSession, side: Side, stream: STTStream) -> None:
        self.session = session
        self.side = side
        self._stream = stream
        self._audio = bytearray()
        self._max_bytes = MAX_UTTERANCE_S * SAMPLE_RATE * SAMPLE_WIDTH
        self._closed = False

    @property
    def duration_ms(self) -> int:
        return len(self._audio) * 1000 // (SAMPLE_RATE * SAMPLE_WIDTH)

    async def push(self, frame: bytes) -> None:
        if self._closed or len(self._audio) >= self._max_bytes:
            return
        frame = frame[: self._max_bytes - len(self._audio)]
        self._audio += frame
        await self._stream.push(frame)

    async def cancel(self) -> None:
        if not self._closed:
            self._closed = True
            await self._stream.aclose()

    async def finish(self) -> WardTurn:
        turn, audio = await self.finish_streaming()
        async for _ in audio:
            pass
        return turn

    async def finish_streaming(self) -> tuple[WardTurn, AsyncIterator[bytes]]:
        """Run STT end and MT, then return the turn (texts, warning, stt/mt timings) and
        an iterator of TTS chunks. Draining the iterator fills turn.audio and the tts
        timings. The turn is in the transcript as soon as this returns."""
        s = self.session
        src_lang = s.pair.side(self.side).lang
        tgt_side = other(self.side)
        tgt_lang = s.pair.side(tgt_side).lang
        clock = s.clock
        t0 = clock.now_ms()
        self._closed = True
        try:
            src_text = (await self._stream.end()).strip()
        finally:
            await self._stream.aclose()
        t_stt = clock.now_ms()
        turn = WardTurn(
            side=self.side,
            src_text=src_text,
            tgt_text="",
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            at=_now_iso(),
            timings={"utt_ms": self.duration_ms, "stt_ms": t_stt - t0},
        )
        if not src_text:
            return turn, _empty()
        mt_lang = s.pair.side(self.side).mt_lang  # what the STT outputs (pivot: en-IN)
        if mt_lang == tgt_lang:  # e.g. Marathi speech -> English final for English: no MT
            turn.tgt_text, t_mt = src_text, t_stt
        else:
            turn.tgt_text = (await s.mt.translate(src_text, mt_lang, tgt_lang)).strip()
            t_mt = clock.now_ms()
        turn.timings["mt_ms"] = t_mt - t_stt
        turn.numbers = extract_numbers(src_text)
        turn.warning = check_numbers(src_text, turn.tgt_text)
        turn.voice = s._voice(self.side, bytes(self._audio))
        s._record(turn)
        tts = s.providers.tts[tgt_side]
        return turn, self._speak(turn, tts.synth(turn.tgt_text, turn.voice, tgt_lang), t0, t_mt)

    async def _speak(
        self, turn: WardTurn, chunks: AsyncIterator[bytes], t0: int, t_mt: int
    ) -> AsyncIterator[bytes]:
        clock = self.session.clock
        audio = bytearray()
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                if not audio:
                    now = clock.now_ms()
                    turn.timings["tts_ttfb_ms"] = now - t_mt
                    turn.timings["first_audio_ms"] = now - t0
                audio += chunk
                yield chunk
        finally:
            aclose = getattr(chunks, "aclose", None)
            if aclose is not None:
                await aclose()
            turn.audio = bytes(audio)
            turn.timings["tts_ms"] = clock.now_ms() - t_mt
            turn.timings.setdefault("tts_ttfb_ms", turn.timings["tts_ms"])
            turn.timings.setdefault("first_audio_ms", clock.now_ms() - t0)


async def _empty() -> AsyncIterator[bytes]:
    return
    yield b""  # pragma: no cover


def _now_iso() -> str:
    return datetime.fromtimestamp(time.time(), UTC).isoformat(timespec="seconds")
