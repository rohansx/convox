from __future__ import annotations

import io
from collections.abc import AsyncIterator

import httpx
import structlog

from convox.config import settings
from convox.providers.base import STTProvider

logger = structlog.get_logger()

SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"

# Sarvam STT supported languages (saarika:v2.5)
SUPPORTED_LANGUAGES = [
    "hi-IN", "bn-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN",
    "pa-IN", "ta-IN", "te-IN", "en-IN", "gu-IN", "unknown",
]


class SarvamSTT(STTProvider):
    provider_id = "sarvam"
    supported_languages = SUPPORTED_LANGUAGES

    def __init__(self, api_key: str | None = None, model: str = "saarika:v2.5") -> None:
        self.api_key = api_key or settings.sarvam_api_key
        self.model = model
        if not self.api_key:
            raise ValueError("Sarvam API key is required. Set CONVOX_SARVAM_API_KEY.")

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "unknown",
        content_type: str = "audio/wav",
    ) -> dict:
        """Transcribe audio bytes using Sarvam REST API. Returns transcript dict."""
        filename = "audio.wav"
        if "mp3" in content_type:
            filename = "audio.mp3"
        elif "ogg" in content_type:
            filename = "audio.ogg"
        elif "flac" in content_type:
            filename = "audio.flac"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                SARVAM_STT_URL,
                headers={"api-subscription-key": self.api_key},
                files={"file": (filename, io.BytesIO(audio_data), content_type)},
                data={
                    "model": self.model,
                    "language_code": language,
                },
            )

        if response.status_code != 200:
            logger.error("sarvam_stt_error", status=response.status_code, body=response.text[:200])
            raise RuntimeError(f"Sarvam STT error: {response.status_code} - {response.text[:200]}")

        result = response.json()
        logger.info(
            "sarvam_stt_success",
            language=result.get("language_code"),
            transcript_len=len(result.get("transcript", "")),
        )
        return {
            "transcript": result.get("transcript", ""),
            "language_code": result.get("language_code"),
            "language_probability": result.get("language_probability"),
            "timestamps": result.get("timestamps"),
            "request_id": result.get("request_id"),
        }

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        language: str = "unknown",
        sample_rate: int = 16000,
    ) -> AsyncIterator[dict]:
        """Accumulate audio chunks and transcribe. Yields transcript dicts."""
        buffer = bytearray()
        async for chunk in audio_stream:
            buffer.extend(chunk)

        if buffer:
            result = await self.transcribe(bytes(buffer), language)
            yield result

    def cost_per_second(self) -> float:
        # Sarvam pricing: approximately ₹0.08/min ≈ $0.001/min ≈ $0.0000167/sec
        return 0.0000167
