"""Console routes: server-rendered pages plus the SSE stream. Live data comes from the
registry (snapshot) and the EventBus (updates), never from the database."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from convox.api.deps import CallRegistry, get_bus, get_registry, require_console_auth
from convox.console.sse import sse_stream
from convox.events import EventBus

CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"
TEMPLATES_DIR = CONSOLE_DIR / "templates"
STATIC_DIR = CONSOLE_DIR / "static"

TARGET_P50_MS = 1500
TARGET_P95_MS = 2000

LANG_NAMES = {
    "hi": "Hindi", "kn": "Kannada", "ta": "Tamil", "te": "Telugu", "ml": "Malayalam",
    "mr": "Marathi", "bn": "Bengali", "gu": "Gujarati", "pa": "Punjabi", "od": "Odia",
    "or": "Odia", "en": "English",
}  # fmt: skip

STAGES = (
    ("eou", "t_speech_end", "t_eou"),
    ("stt", "t_eou", "t_stt_final"),
    ("mt", "t_stt_final", "t_mt"),
    ("tts", "t_mt", "t_tts_ttfb"),
    ("play", "t_tts_ttfb", "t_play_start"),
)


def mask_number(number: str | None) -> str:
    if not number:
        return ""
    digits = number.strip()
    if len(digits) <= 6:
        return "•" * len(digits)
    return f"{digits[:3]} {digits[3:5]}{'•' * (len(digits) - 8)}{digits[-3:]}"


def lang_name(code: str | None) -> str:
    if not code:
        return ""
    return LANG_NAMES.get(code.split("-")[0].lower(), code)


def grade(ms: int | None) -> str:
    if ms is None:
        return ""
    if ms < TARGET_P50_MS:
        return "good"
    return "warn" if ms < TARGET_P95_MS else "bad"


def secs(ms: int | None) -> str:
    return "—" if ms is None else f"{ms / 1000:.2f} s"


def turn_view(turn: dict[str, Any]) -> dict[str, Any]:
    stages = []
    for name, a, b in STAGES:
        if turn.get(a) is not None and turn.get(b) is not None:
            stages.append((name, turn[b] - turn[a]))
    total = None
    if turn.get("t_play_start") is not None and turn.get("t_speech_end") is not None:
        total = turn["t_play_start"] - turn["t_speech_end"]
    return {**turn, "stages": stages, "first_audio_ms": total, "grade": grade(total)}


def timeline(call: dict[str, Any]) -> list[dict[str, Any]]:
    """Turns and markers in call order. Turns sort by speech end, markers by ts."""
    items: list[tuple[int, int, dict[str, Any]]] = []
    for i, t in enumerate(call.get("turns", [])):
        at = t.get("t_speech_end") or t.get("t_speech_start") or 0
        items.append((at, i, {"type": "turn", **turn_view(t)}))
    for i, m in enumerate(call.get("markers", [])):
        items.append((m.get("ts_ms") or 0, 10**9 + i, {"type": "marker", **m}))
    items.sort(key=lambda x: (x[0], x[1]))
    return [x[2] for x in items]


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters.update(mask=mask_number, lang=lang_name, secs=secs, grade=grade)

router = APIRouter(dependencies=[Depends(require_console_auth)])
Registry = Annotated[CallRegistry, Depends(get_registry)]


@router.get("/console", response_class=HTMLResponse)
async def index(request: Request, registry: Registry) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"calls": registry.list_calls()})


@router.get("/console/_calls", response_class=HTMLResponse)
async def calls_rows(request: Request, registry: Registry) -> HTMLResponse:
    return templates.TemplateResponse(request, "_calls_rows.html", {"calls": registry.list_calls()})


@router.get("/console/calls/{call_id}", response_class=HTMLResponse)
async def call_view(request: Request, call_id: str, registry: Registry) -> HTMLResponse:
    call = registry.get_call(call_id)
    if call is None:
        raise HTTPException(404, "no such call")
    views = [turn_view(t) for t in call.get("turns", []) if not t.get("backchannel")]
    totals = {v["seq"]: v["first_audio_ms"] for v in views if v["first_audio_ms"] is not None}
    return templates.TemplateResponse(
        request,
        "call.html",
        {
            "call": call,
            "items": timeline(call),
            "n_turns": len(views),
            "totals": totals,
            "targets": {"p50": TARGET_P50_MS, "p95": TARGET_P95_MS},
        },
    )


@router.get("/console/events")
async def events(
    bus: Annotated[EventBus, Depends(get_bus)],
    call: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    return StreamingResponse(
        sse_stream(bus, call),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
