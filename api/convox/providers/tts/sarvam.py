from __future__ import annotations

import base64
from collections.abc import AsyncIterator

import httpx
import structlog

from convox.config import settings
from convox.providers.base import TTSProvider

logger = structlog.get_logger()

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"

SUPPORTED_LANGUAGES = [
    "hi-IN", "bn-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN",
    "pa-IN", "ta-IN", "te-IN", "en-IN", "gu-IN",
]

DEFAULT_SPEAKERS = {
    "hi-IN": "priya",
    "bn-IN": "priya",
    "ta-IN": "priya",
    "te-IN": "priya",
    "kn-IN": "priya",
    "ml-IN": "priya",
    "mr-IN": "priya",
    "gu-IN": "priya",
    "pa-IN": "priya",
    "od-IN": "priya",
    "en-IN": "priya",
}


class SarvamTTS(TTSProvider):
    provider_id = "sarvam"
    supported_languages = SUPPORTED_LANGUAGES

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "bulbul:v3",
        speaker: str | None = None,
        sample_rate: int = 16000,
        output_codec: str = "wav",
    ) -> None:
        self.api_key = api_key or settings.sarvam_api_key
        self.model = model
        self.default_speaker = speaker
        self.sample_rate = sample_rate
        self.output_codec = output_codec
        if not self.api_key:
            raise ValueError("Sarvam API key is required. Set CONVOX_SARVAM_API_KEY.")

    async def synthesize(
        self,
        text: str,
        language: str = "hi-IN",
        speaker: str | None = None,
    ) -> bytes:
        """Convert text to speech. Returns raw audio bytes."""
        voice = speaker or self.default_speaker or DEFAULT_SPEAKERS.get(language, "anushka")

        payload = {
            "text": text[:2500],  # Sarvam limit for bulbul:v3
            "target_language_code": language,
            "model": self.model,
            "speaker": voice,
            "speech_sample_rate": str(self.sample_rate),
            "output_audio_codec": self.output_codec,
        }

        if self.model == "bulbul:v3":
            payload["temperature"] = 0.6
            payload["pace"] = 1.0

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                SARVAM_TTS_URL,
                headers={
                    "api-subscription-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if response.status_code != 200:
            logger.error("sarvam_tts_error", status=response.status_code, body=response.text[:200])
            raise RuntimeError(f"Sarvam TTS error: {response.status_code} - {response.text[:200]}")

        result = response.json()
        audios = result.get("audios", [])
        if not audios:
            raise RuntimeError("Sarvam TTS returned no audio")

        audio_bytes = base64.b64decode(audios[0])
        logger.info(
            "sarvam_tts_success",
            language=language,
            speaker=voice,
            text_len=len(text),
            audio_bytes=len(audio_bytes),
        )
        return audio_bytes

    async def synthesize_stream(
        self,
        text_stream: AsyncIterator[str],
        voice: str = "priya",
    ) -> AsyncIterator[bytes]:
        """Accumulate text chunks and synthesize. Yields audio bytes."""
        buffer = []
        async for chunk in text_stream:
            buffer.append(chunk)

        full_text = "".join(buffer)
        if full_text.strip():
            audio = await self.synthesize(full_text, speaker=voice)
            yield audio

    def cost_per_character(self) -> float:
        # Sarvam pricing: approximately ₹0.06/min ≈ ~150 chars/min
        # ≈ $0.0007/min ≈ $0.0000047/char
        return 0.0000047
