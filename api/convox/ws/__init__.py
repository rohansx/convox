from __future__ import annotations

import asyncio
import json
import uuid

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from convox.database.postgres import get_pool
from convox.middleware.auth import _resolve_user_id
from convox.model import SessionCreate
from convox.providers import get_llm_provider, get_stt_provider, get_tts_provider
from convox.repository import SessionRepo, TranscriptRepo
from convox.service.cost import CostTracker

logger = structlog.get_logger()

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/voice/{agent_id}")
async def voice_pipeline(ws: WebSocket, agent_id: uuid.UUID) -> None:
    """Real-time voice pipeline over WebSocket.

    Protocol:
    1. Client sends JSON: {"type": "auth", "api_key": "cvx_..."}
    2. Client sends JSON: {"type": "config", "language": "hi-IN", ...}
    3. Client sends binary audio frames (PCM 16-bit, 16kHz mono)
    4. Client sends JSON: {"type": "end_turn"} when user stops speaking
    5. Server responds with transcripts + binary TTS audio
    6. Client sends {"type": "end_session"} to close
    """
    await ws.accept()

    # ── Auth ──
    try:
        auth_msg = await asyncio.wait_for(ws.receive_json(), timeout=10.0)
    except (TimeoutError, WebSocketDisconnect):
        await ws.close(code=4001, reason="Auth timeout")
        return

    if auth_msg.get("type") != "auth" or not auth_msg.get("api_key"):
        await ws.close(code=4001, reason="First message must be auth")
        return

    user_id = await _resolve_user_id(auth_msg["api_key"])
    if not user_id:
        await ws.close(code=4003, reason="Invalid API key")
        return

    # ── Config ──
    try:
        config_msg = await asyncio.wait_for(ws.receive_json(), timeout=10.0)
    except (TimeoutError, WebSocketDisconnect):
        await ws.close(code=4002, reason="Config timeout")
        return

    language = config_msg.get("language", "hi-IN")
    stt_id = config_msg.get("stt", "sarvam")
    llm_id = config_msg.get("llm", "openai")
    tts_id = config_msg.get("tts", "sarvam")
    tts_speaker = config_msg.get("tts_speaker", "priya")
    system_prompt = config_msg.get(
        "system_prompt", "You are a helpful voice assistant. Reply concisely.",
    )

    try:
        stt = get_stt_provider(stt_id)
        llm = get_llm_provider(llm_id)
        tts = get_tts_provider(tts_id)
    except ValueError as e:
        await ws.close(code=4004, reason=str(e))
        return

    # ── Create session ──
    pool = get_pool()
    session_repo = SessionRepo(pool)
    transcript_repo = TranscriptRepo(pool)
    cost_tracker = CostTracker()

    session = await session_repo.create(
        SessionCreate(agent_id=agent_id, direction="inbound"),
        user_id,
    )
    await session_repo.update_status(session.id, "active")

    await ws.send_json({"type": "session_started", "session_id": str(session.id)})
    logger.info("ws_session_started", session_id=str(session.id), agent_id=str(agent_id))

    # ── Conversation loop ──
    conversation: list[dict] = []
    turn_index = 0

    try:
        while True:
            audio_buffer = bytearray()

            # Collect audio frames until end_turn
            while True:
                msg = await ws.receive()

                if msg.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect()

                if "bytes" in msg and msg["bytes"]:
                    audio_buffer.extend(msg["bytes"])
                    continue

                if "text" in msg and msg["text"]:
                    data = json.loads(msg["text"])
                    if data.get("type") == "end_turn":
                        break
                    if data.get("type") == "end_session":
                        raise WebSocketDisconnect()

            if not audio_buffer:
                continue

            # ── STT ──
            audio_duration_sec = len(audio_buffer) / (16000 * 2)  # 16kHz, 16-bit
            stt_result = await stt.transcribe(bytes(audio_buffer), language)
            user_text = stt_result.get("transcript", "")

            if not user_text.strip():
                await ws.send_json({"type": "silence"})
                continue

            await cost_tracker.record_stt(
                session.id, stt_id, audio_duration_sec, stt.cost_per_second(),
            )

            await transcript_repo.create(
                session.id, turn_index, "user", user_text,
                int(audio_duration_sec * 1000), stt_id, None, 0,
            )
            turn_index += 1

            await ws.send_json({"type": "transcript", "text": user_text, "speaker": "user"})

            # ── LLM ──
            conversation.append({"role": "user", "content": user_text})
            await ws.send_json({"type": "response_start"})

            assistant_text = ""
            async for chunk in llm.chat_stream(conversation, system_prompt):
                assistant_text += chunk

            conversation.append({"role": "assistant", "content": assistant_text})

            # Estimate token usage (rough: 1 token ≈ 4 chars)
            input_tokens = sum(len(m["content"]) for m in conversation[:-1]) // 4
            output_tokens = len(assistant_text) // 4
            input_cost, output_cost = llm.cost_per_1k_tokens()
            await cost_tracker.record_llm(
                session.id, llm_id, input_tokens, output_tokens,
                input_cost, output_cost,
            )

            await transcript_repo.create(
                session.id, turn_index, "assistant", assistant_text,
                0, None, None, 0,
            )
            turn_index += 1

            await ws.send_json(
                {"type": "transcript", "text": assistant_text, "speaker": "assistant"},
            )

            # ── TTS ──
            tts_audio = await tts.synthesize(assistant_text, language, tts_speaker)
            await cost_tracker.record_tts(
                session.id, tts_id, len(assistant_text), tts.cost_per_character(),
            )

            # Send audio in 8KB chunks
            chunk_size = 8192
            for i in range(0, len(tts_audio), chunk_size):
                await ws.send_bytes(tts_audio[i : i + chunk_size])

            await ws.send_json({"type": "response_end"})

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws_error", session_id=str(session.id))
    finally:
        await session_repo.update_status(session.id, "completed")
        logger.info("ws_session_ended", session_id=str(session.id), turns=turn_index)
