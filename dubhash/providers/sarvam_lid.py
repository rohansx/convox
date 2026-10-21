"""Sarvam spoken-language identification (LanguageID, dubhash/interp/langid.py).

Speech: the REST STT `POST /speech-to-text` with `language_code=unknown` auto-detects and
answers `language_code` plus `language_probability` (0..1). We send the first `window_ms`
of the utterance as a 16 kHz WAV. With `second_model` the same audio goes to a second
model in parallel, and `decide()` accepts the language when both models agree, or when the
primary alone clears its threshold. Text (optional): `POST /text-lid` names the language
of a transcript (no confidence); with `text_check=True` a disagreement vetoes the guess.
Measured numbers and the chosen defaults: providers/README.md ("Language identification")
and docs/plan/open-questions.md row L8.

https://docs.sarvam.ai/api-reference/speech-to-text/transcribe
https://docs.sarvam.ai/api-reference/text/identify-language

Errors never raise: the session treats lang=None as "not sure" and falls back (keypad
menu), so a vendor failure is logged as `lid.error` and answered with None.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import httpx

from dubhash.audio.frames import SAMPLE_RATE, ms_to_bytes
from dubhash.audio.wav import pcm_to_wav_bytes
from dubhash.interp.langid import LangGuess, normalise, pick

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.sarvam.ai"
# saaras:v3's confidence is calibrated: on 8 kHz mu-law audio (L8) no Indic guess at >= 0.7
# was wrong. The wrong guesses that pass 0.7 are en-IN on loanword-only utterances ("Flat
# number 302", "Cash on delivery ₹400", up to 0.93 on wideband audio), so English needs
# more. saaras:v4 is right more often but says 0.99 when wrong, so it only ever votes.
DEFAULT_MODEL = "saaras:v3"
DEFAULT_SECOND_MODEL = "saaras:v4"
DEFAULT_THRESHOLD = 0.7
DEFAULT_LANG_THRESHOLDS: dict[str, float] = {"en-IN": 0.95}
DEFAULT_WINDOW_MS = 2000
KEEPALIVE_S = 60.0  # as SarvamMT: keep the TLS connection between turns


@dataclass(frozen=True, slots=True)
class Detection:
    """One model's raw speech answer, before candidates and threshold are applied."""

    lang: str | None
    confidence: float
    transcript: str
    latency_ms: int


def decide(
    primary: Detection,
    second: Detection | None,
    candidates: Sequence[str],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    lang_thresholds: Mapping[str, float] = DEFAULT_LANG_THRESHOLDS,
) -> LangGuess:
    """Both models name the same candidate -> that language (confidence: the higher of
    the two). Otherwise the primary alone, if it clears its language's threshold."""
    if second is not None and primary.lang is not None and primary.lang == second.lang:
        agreed = pick(primary.lang, max(primary.confidence, second.confidence), candidates,
                      threshold=0.0)  # fmt: skip
        if agreed.lang is not None:
            return agreed
    t = lang_thresholds.get(primary.lang or "", threshold)
    return pick(primary.lang, primary.confidence, candidates, threshold=t)


class SarvamLID:
    name = "sarvam"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        second_model: str | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        lang_thresholds: dict[str, float] | None = None,
        window_ms: int | None = DEFAULT_WINDOW_MS,
        text_check: bool = False,
        timeout_s: float = 5.0,
    ) -> None:
        self._key = api_key
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s, limits=httpx.Limits(keepalive_expiry=KEEPALIVE_S)
        )
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self.model = model
        self.second_model = second_model
        self.threshold = threshold
        self.lang_thresholds = dict(
            DEFAULT_LANG_THRESHOLDS if lang_thresholds is None else lang_thresholds
        )
        self.window_ms = window_ms
        self.text_check = text_check

    async def identify(self, pcm16_16k: bytes, candidates: Sequence[str]) -> LangGuess:
        if self.window_ms is not None:
            pcm16_16k = pcm16_16k[: ms_to_bytes(self.window_ms)]
        if not pcm16_16k:
            return LangGuess(None, 0.0, "lid")
        t0 = time.monotonic()
        calls = [self.detect(pcm16_16k)]
        if self.second_model:
            calls.append(self.detect(pcm16_16k, model=self.second_model))
        results = await asyncio.gather(*calls, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException) and not isinstance(r, _Failed):
                raise r
        first = results[0]
        if not isinstance(first, Detection):
            return LangGuess(None, 0.0, "lid")
        second = results[1] if len(results) > 1 else None
        second = second if isinstance(second, Detection) else None
        guess = decide(first, second, candidates, threshold=self.threshold,
                       lang_thresholds=self.lang_thresholds)  # fmt: skip
        if guess.lang is not None and self.text_check and first.transcript.strip():
            try:
                text_lang = await self.text_lid(first.transcript)
            except _Failed:
                text_lang = None
            if text_lang != guess.lang:
                log.info("lid.veto", extra={"provider": self.name, "speech": guess.lang})
                return LangGuess(None, guess.confidence, "lid")
        log.info(
            "lid.done",
            extra={
                "provider": self.name,
                "lang": guess.lang,
                "detected": first.lang,
                "second": second.lang if second else None,
                "confidence": guess.confidence,
                "latency_ms": int((time.monotonic() - t0) * 1000),
            },
        )
        return guess

    async def detect(self, pcm16_16k: bytes, *, model: str | None = None) -> Detection:
        """One speech-LID call on all of `pcm16_16k` (no window, no candidates)."""
        t0 = time.monotonic()
        wav = pcm_to_wav_bytes(pcm16_16k, SAMPLE_RATE)
        body = await self._post(
            "/speech-to-text",
            files={"file": ("utterance.wav", wav, "audio/wav")},
            data={"model": model or self.model, "language_code": "unknown"},
        )
        prob = body.get("language_probability")
        return Detection(
            lang=normalise(body.get("language_code")),
            confidence=float(prob) if isinstance(prob, int | float) else 0.0,
            transcript=str(body.get("transcript") or ""),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def text_lid(self, text: str) -> str | None:
        """Sarvam text LID on a transcript (max 1000 chars). No confidence is given."""
        body = await self._post("/text-lid", json={"input": text[:1000]})
        return normalise(body.get("language_code"))

    async def _post(self, path: str, **kw: object) -> dict:
        try:
            r = await self._client.post(
                f"{self._base}{path}",
                headers={"api-subscription-key": self._key},
                timeout=self._timeout,
                **kw,  # type: ignore[arg-type]
            )
        except httpx.HTTPError as e:
            log.warning("lid.error", extra={"provider": self.name, "error": type(e).__name__})
            raise _Failed() from e
        if r.status_code >= 400:
            log.warning("lid.error", extra={"provider": self.name, "status": r.status_code})
            raise _Failed()
        try:
            body = r.json()
        except ValueError as e:
            log.warning("lid.error", extra={"provider": self.name, "error": "malformed"})
            raise _Failed() from e
        return body if isinstance(body, dict) else {}

    async def prewarm(self) -> None:
        """Open the HTTPS connection ahead of the first identify(); errors are ignored."""
        try:
            await self._client.head(f"{self._base}/", timeout=self._timeout)
        except httpx.HTTPError:
            pass

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()


class _Failed(Exception):
    pass
