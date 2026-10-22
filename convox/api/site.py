"""The public face of convox and the stage dashboard.

GET /                 landing page (public): the problem, how convox solves it, two
                      animated phones, languages, use cases
GET /demo/preview     the dashboard driven by a scripted sample call (public, labelled
                      "Sample data"; creates no call, opens no stream)
GET /demo             pick a pair (console auth); the form posts to POST /join with
                      view=demo, which redirects to /demo/{call_id}
GET /demo/{call_id}   the stage dashboard (console auth): QR codes until both phones
                      join, then the live conversation, per-turn pipeline, floor lane
                      and event ticker from the console SSE (/console/events?call=)
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from convox.api.calls import get_pairs
from convox.api.console import TARGET_P50_MS, TARGET_P95_MS, templates
from convox.api.deps import CallRegistry, get_registry, require_console_auth
from convox.api.loopback import require_enabled
from convox.config.pairs import PairConfig
from convox.join import routes as join

public = APIRouter()
demo = APIRouter(dependencies=[Depends(require_enabled), Depends(require_console_auth)])

Pairs = Annotated[dict[str, PairConfig], Depends(get_pairs)]
Registry = Annotated[CallRegistry, Depends(get_registry)]

# Landing page language strip, stage order (native, English).
LANDING_LANGS = (
    ("मराठी", "Marathi"),
    ("हिन्दी", "Hindi"),
    ("ગુજરાતી", "Gujarati"),
    ("English", "English"),
    ("ಕನ್ನಡ", "Kannada"),
    ("தமிழ்", "Tamil"),
)

# The sample call /demo/preview plays: a delivery agent (Hindi) and a customer (Marathi).
# Marathi lines need a native speaker's check (PQ5).
PREVIEW_SIDES = (
    {"side": "a", "lang": "mr-IN", "native": "मराठी", "english": "Marathi"},
    {"side": "b", "lang": "hi-IN", "native": "हिन्दी", "english": "Hindi"},
)


def _targets() -> dict[str, int]:
    return {"p50": TARGET_P50_MS, "p95": TARGET_P95_MS}


@public.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "landing.html", {"langs": LANDING_LANGS})


@public.get("/demo/preview", response_class=HTMLResponse)
async def demo_preview(request: Request, embed: bool = False) -> HTMLResponse:
    # embed=1: the landing page's framed copy, sized to a fixed frame instead of the window
    return templates.TemplateResponse(
        request,
        "demo.html",
        {
            "call": {"call_id": "preview", "state": "IDLE", "status": "active", "legs": {}},
            "sides": list(PREVIEW_SIDES),
            "turns_json": "[]",
            "events_url": None,
            "preview": True,
            "embed": embed,
            "targets": _targets(),
        },
    )


@demo.get("/demo", response_class=HTMLResponse)
async def demo_picker(request: Request, pairs: Pairs) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "demo.html", {"call": None, "pairs": join._pair_views(pairs)}
    )


@demo.get("/demo/{call_id}", response_class=HTMLResponse)
async def demo_dashboard(request: Request, call_id: str, registry: Registry) -> HTMLResponse:
    call = registry.get_call(call_id)
    if call is None:
        raise HTTPException(404, "no such call")
    settings = request.app.state.settings
    sides: list[dict[str, Any]] = []
    for side in ("a", "b"):
        lang = call.get(f"{side}_lang") or ""
        url = join.phone_url(settings, call_id, side)
        sides.append({"side": side, **join._side_view(lang), "url": url, "svg": join.qr_svg(url)})
    turns = [t for t in call.get("turns", []) if not t.get("backchannel")]
    return templates.TemplateResponse(
        request,
        "demo.html",
        {
            "call": call,
            "sides": sides,
            "turns_json": json.dumps(turns, ensure_ascii=False, default=str),
            "events_url": f"/console/events?call={call_id}",
            "preview": False,
            "targets": _targets(),
        },
    )
