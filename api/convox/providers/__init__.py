from __future__ import annotations

import structlog

from convox.config import settings
from convox.providers.base import STTProvider, TTSProvider

logger = structlog.get_logger()


def get_stt_provider(provider_id: str = "sarvam", **kwargs: object) -> STTProvider:
    if provider_id == "sarvam":
        from convox.providers.stt.sarvam import SarvamSTT
        return SarvamSTT(**kwargs)
    raise ValueError(f"Unknown STT provider: {provider_id}")


def get_tts_provider(provider_id: str = "sarvam", **kwargs: object) -> TTSProvider:
    if provider_id == "sarvam":
        from convox.providers.tts.sarvam import SarvamTTS
        return SarvamTTS(**kwargs)
    raise ValueError(f"Unknown TTS provider: {provider_id}")


def list_available_providers() -> dict:
    """List all available providers and their status."""
    providers = {
        "stt": [
            {"id": "sarvam", "name": "Sarvam AI", "configured": bool(settings.sarvam_api_key)},
            {"id": "deepgram", "name": "Deepgram", "configured": bool(settings.deepgram_api_key)},
        ],
        "tts": [
            {"id": "sarvam", "name": "Sarvam AI", "configured": bool(settings.sarvam_api_key)},
            {"id": "elevenlabs", "name": "ElevenLabs", "configured": bool(settings.elevenlabs_api_key)},
        ],
        "llm": [
            {"id": "openai", "name": "OpenAI", "configured": bool(settings.openai_api_key)},
            {"id": "anthropic", "name": "Anthropic Claude", "configured": bool(settings.anthropic_api_key)},
        ],
    }
    return providers
