"""Sarvam translate (Mayura) over REST. https://docs.sarvam.ai/api-reference/text/translate-text"""

from __future__ import annotations

import logging
import time

import httpx

from convox.providers.errors import ProviderError

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.sarvam.ai"
PIVOT = "en-IN"
# httpx drops an idle keep-alive connection after 5 s by default, so most turns of a real
# conversation (one MT call every few seconds per direction) paid a new TLS handshake to
# Sarvam. Keep it for a minute; prewarm() opens it when the call is created (L7).
KEEPALIVE_S = 60.0
# Sarvam REST uses od-IN for Odia; the realtime STT socket uses or-IN.
_LANG_ALIASES = {"or-IN": "od-IN"}


def _code(lang: str) -> str:
    return _LANG_ALIASES.get(lang, lang)


class SarvamMT:
    name = "sarvam"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 5.0,
        model: str = "mayura:v1",
        mode: str = "modern-colloquial",
    ) -> None:
        self._key = api_key
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s, limits=httpx.Limits(keepalive_expiry=KEEPALIVE_S)
        )
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._model = model
        self._mode = mode
        # Pairs the API rejected directly (Mayura is documented as English <-> Indic);
        # these go through an English pivot from then on.
        self._pivot_pairs: set[tuple[str, str]] = set()

    async def prewarm(self) -> None:
        """Open the HTTPS connection (DNS, TCP, TLS) ahead of the first translation. Any
        answer will do (the root is a 404); errors are ignored."""
        t0 = time.monotonic()
        try:
            await self._client.head(f"{self._base}/", timeout=self._timeout)
        except httpx.HTTPError as e:
            log.info("mt.prewarm_failed", extra={"provider": self.name, "error": repr(e)})
            return
        log.info(
            "mt.prewarm",
            extra={"provider": self.name, "latency_ms": int((time.monotonic() - t0) * 1000)},
        )

    async def translate(self, text: str, src: str, tgt: str) -> str:
        if not text.strip():
            return ""
        src, tgt = _code(src), _code(tgt)
        if src == tgt:
            return text
        t0 = time.monotonic()
        if (src, tgt) in self._pivot_pairs:
            out = await self._pivot(text, src, tgt)
        else:
            try:
                out = await self._call(text, src, tgt)
            except _Rejected:
                if PIVOT in (src, tgt):
                    raise ProviderError(f"sarvam translate rejected {src}->{tgt}") from None
                log.warning("mt.pivot", extra={"provider": self.name, "pair": f"{src}->{tgt}"})
                self._pivot_pairs.add((src, tgt))
                out = await self._pivot(text, src, tgt)
        log.info(
            "mt.done",
            extra={"provider": self.name, "latency_ms": int((time.monotonic() - t0) * 1000)},
        )
        return out

    async def _pivot(self, text: str, src: str, tgt: str) -> str:
        mid = await self._call(text, src, PIVOT)
        return await self._call(mid, PIVOT, tgt)

    async def _call(self, text: str, src: str, tgt: str) -> str:
        body = {
            "input": text,
            "source_language_code": src,
            "target_language_code": tgt,
            "mode": self._mode,
            "model": self._model,
            "numerals_format": "international",  # keep digits (ADR 0007)
        }
        try:
            r = await self._client.post(
                f"{self._base}/translate",
                json=body,
                headers={"api-subscription-key": self._key},
                timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"sarvam translate: {type(e).__name__}") from e
        if r.status_code == 400:
            raise _Rejected()
        if r.status_code >= 400:
            raise ProviderError(f"sarvam translate: HTTP {r.status_code}")
        try:
            return str(r.json()["translated_text"])
        except (ValueError, KeyError) as e:
            raise ProviderError("sarvam translate: malformed response") from e

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()


class _Rejected(Exception):
    pass
