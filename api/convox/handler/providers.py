from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from convox.middleware.auth import get_current_user_id
from convox.providers import get_llm_provider, get_stt_provider, get_tts_provider, list_available_providers

router = APIRouter(prefix="/v1/providers", tags=["providers"])


@router.get("")
async def list_providers(
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> dict:
    return list_available_providers()


@router.post("/test/stt")
async def test_stt(
    file: UploadFile = File(...),
    language: str = Form("unknown"),
    provider: str = Form("sarvam"),
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> dict:
    """Test STT provider with an audio file upload."""
    try:
        stt = get_stt_provider(provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    audio_data = await file.read()
    if not audio_data:
        raise HTTPException(status_code=400, detail="Empty audio file")

    try:
        result = await stt.transcribe(audio_data, language, file.content_type or "audio/wav")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"provider": provider, "result": result}


@router.post("/test/tts")
async def test_tts(
    text: str = Form(...),
    language: str = Form("hi-IN"),
    speaker: str = Form("priya"),
    provider: str = Form("sarvam"),
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> dict:
    """Test TTS provider with text input. Returns base64 audio."""
    try:
        tts = get_tts_provider(provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        audio_bytes = await tts.synthesize(text, language, speaker)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "provider": provider,
        "language": language,
        "speaker": speaker,
        "audio_base64": base64.b64encode(audio_bytes).decode(),
        "audio_size_bytes": len(audio_bytes),
    }


class LLMTestRequest(BaseModel):
    message: str = "Hello, how are you?"
    system_prompt: str = "You are a helpful voice assistant for an Indian bank. Reply concisely in 1-2 sentences."
    provider: str = "openai"


@router.post("/test/llm")
async def test_llm(
    body: LLMTestRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> dict:
    """Test LLM provider with a chat message."""
    try:
        llm = get_llm_provider(body.provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    messages = [{"role": "user", "content": body.message}]
    try:
        result = await llm.chat(messages, body.system_prompt)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"provider": body.provider, "result": result}
