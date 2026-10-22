"""ElevenLabs Flash v2.5 over HTTP streaming, raw PCM16 @16k (output_format=pcm_16000).

HTTP streaming, not the stream-input websocket: the whole sentence is known up front, and
ElevenLabs documents the websocket as slightly slower in that case (it buffers text).
https://elevenlabs.io/docs/api-reference/text-to-speech/stream
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator

import httpx

from convox.providers.errors import ProviderError

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.elevenlabs.io"
MODEL_ID = "eleven_flash_v2_5"

# Placeholder ids from the spec -> voice-library ids. Still need the listening test (Q3).
VOICE_ALIASES = {
    "hi-male-1": "jjlrvsvJ1M0ZMRNgZuLO",  # "Karan": friendly, polite, soothing
    "hi-female-1": "KYiVPerWcenyBTIvWbfY",  # "Sia": friendly, helpful, reassuring
    "en-male-1": "jjlrvsvJ1M0ZMRNgZuLO",
    "en-female-1": "KYiVPerWcenyBTIvWbfY",
}


def vendor_voice_id(voice: str) -> str:
    """Map "eleven:<id or alias>" to an ElevenLabs voice_id."""
    vid = voice.split(":", 1)[1] if voice.startswith("eleven:") else voice
    return VOICE_ALIASES.get(vid, vid)


class ElevenTTS:
    name = "eleven"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 10.0,
        model_id: str = MODEL_ID,
    ) -> None:
        self._key = api_key
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._model = model_id

    def synth(self, text: str, voice: str, lang: str) -> AsyncIterator[bytes]:
        return self._synth(text, voice, lang)

    async def _synth(self, text: str, voice: str, lang: str) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        vid = vendor_voice_id(voice)
        body = {"text": text, "model_id": self._model, "language_code": lang.split("-")[0]}
        t0 = time.monotonic()
        first = True
        carry = b""
        try:
            async with self._client.stream(
                "POST",
                f"{self._base}/v1/text-to-speech/{vid}/stream",
                params={"output_format": "pcm_16000"},
                json=body,
                headers={"xi-api-key": self._key},
                timeout=self._timeout,
            ) as r:
                if r.status_code >= 400:
                    await r.aread()
                    raise ProviderError(f"eleven tts: HTTP {r.status_code}")
                async for chunk in r.aiter_bytes():
                    data = carry + chunk
                    cut = len(data) - len(data) % 2
                    carry = data[cut:]
                    if not cut:
                        continue
                    if first:
                        first = False
                        log.info(
                            "tts.ttfb",
                            extra={
                                "provider": self.name,
                                "voice": voice,
                                "latency_ms": int((time.monotonic() - t0) * 1000),
                            },
                        )
                    yield data[:cut]
        except httpx.HTTPError as e:
            raise ProviderError(f"eleven tts: {type(e).__name__}") from e

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()
