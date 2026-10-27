"""The public face of convox and the stage dashboard.

GET /                 landing page (public): the problem, how convox solves it, two
                      animated phones, languages, use cases
GET /demo/preview     the live stage playing a scripted Auto call (public, labelled
                      "Sample data"; creates no call, opens no stream); ?embed=1 for the
                      landing page's frame. /demo/preview/details: the dashboard, scripted
GET /demo             pick a story (demo/stories.py) or a language pair (console auth);
                      the forms post to POST /join with view=demo, which redirects to
                      /demo/{call_id}?story=<key>
GET /demo/{call_id}   the live stage for an audience (console auth): role cards with QR
                      codes until both phones join, then each person's column in their
                      own language, the quiet after each line, numbers that came through,
                      and the outcome card. From the console SSE (/console/events?call=)
GET /demo/{call_id}/details   the engineering dashboard: latency metrics, per-turn
                      pipeline, floor lane and event ticker
POST /demo/{call_id}/outcome  what the two people agreed, in each one's language (one
                      Claude call, cached per call and turn count; null without a key)
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from convox.api.calls import get_pairs
from convox.api.console import TARGET_P50_MS, TARGET_P95_MS, templates
from convox.api.deps import CallRegistry, get_registry, require_console_auth
from convox.api.loopback import require_enabled
from convox.config.pairs import PairConfig
from convox.demo import outcome as outcomes
from convox.demo import stories
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


# The stage's people when a call has no story.
GENERIC = {
    "title": "Live call",
    "headline": "Two people, two languages",
    "stakes": "",
    "a": {"role": "Person A", "name": "", "lines": []},
    "b": {"role": "Person B", "name": "", "lines": []},
}


def _stage_story(key: str | None) -> tuple[str | None, dict[str, Any]]:
    found = stories.story(key)
    return (key, found) if found is not None else (None, GENERIC)


def _stage_sides(story: dict[str, Any], langs: dict[str, str], urls: dict[str, str]):
    sides = []
    for side in ("a", "b"):
        url = urls.get(side)
        sides.append(
            {
                "side": side,
                **join._side_view(langs[side]),
                "role": story[side]["role"],
                "name": story[side]["name"],
                "url": url,
                "svg": join.qr_svg(url) if url else None,
            }
        )
    return sides


def _preview_story() -> dict[str, Any]:
    return stories.STORIES["auto"]


@public.get("/demo/preview", response_class=HTMLResponse)
async def stage_preview(request: Request, embed: bool = False) -> HTMLResponse:
    # embed=1: the landing page's framed copy, sized to a fixed frame instead of the window
    story = _preview_story()
    langs = {s: story[s]["lang"] for s in ("a", "b")}
    return templates.TemplateResponse(
        request,
        "stage.html",
        {
            "call": {"call_id": "preview", "state": "IDLE", "status": "active", "legs": {}},
            "story_key": "auto",
            "story": story,
            "sides": _stage_sides(story, langs, {}),
            "turns_json": "[]",
            "events_url": None,
            "outcome_url": None,
            "details_url": "/demo/preview/details",
            "preview": True,
            "embed": embed,
            "targets": _targets(),
        },
    )


@public.get("/demo/preview/details", response_class=HTMLResponse)
async def dashboard_preview(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "demo.html",
        {
            "call": {"call_id": "preview", "state": "IDLE", "status": "active", "legs": {}},
            "sides": list(PREVIEW_SIDES),
            "turns_json": "[]",
            "events_url": None,
            "preview": True,
            "targets": _targets(),
        },
    )


@demo.get("/demo", response_class=HTMLResponse)
async def demo_picker(request: Request, pairs: Pairs) -> HTMLResponse:
    picks = []
    for key, s in stories.STORIES.items():
        picks.append(
            {
                "key": key,
                **s,
                "pair": f"{s['a']['lang']}|{s['b']['lang']}",
                "a_view": join._side_view(s["a"]["lang"]),
                "b_view": join._side_view(s["b"]["lang"]),
            }
        )
    return templates.TemplateResponse(
        request,
        "stage.html",
        {"call": None, "stories": picks, "pairs": join._pair_views(pairs)},
    )


def _call_or_404(registry: CallRegistry, call_id: str) -> dict[str, Any]:
    call = registry.get_call(call_id)
    if call is None:
        raise HTTPException(404, "no such call")
    return call


def _turns(call: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in call.get("turns", []) if not t.get("backchannel")]


@demo.get("/demo/{call_id}", response_class=HTMLResponse)
async def stage(
    request: Request, call_id: str, registry: Registry, story: str | None = None
) -> HTMLResponse:
    call = _call_or_404(registry, call_id)
    key, found = _stage_story(story)
    settings = request.app.state.settings
    suffix = f"&story={key}" if key else ""
    urls = {s: join.phone_url(settings, call_id, s) + suffix for s in ("a", "b")}
    langs = {s: call.get(f"{s}_lang") or "" for s in ("a", "b")}
    q = f"?story={key}" if key else ""
    return templates.TemplateResponse(
        request,
        "stage.html",
        {
            "call": call,
            "story_key": key,
            "story": found,
            "sides": _stage_sides(found, langs, urls),
            "turns_json": json.dumps(_turns(call), ensure_ascii=False, default=str),
            "events_url": f"/console/events?call={call_id}",
            "outcome_url": f"/demo/{call_id}/outcome{q}",
            "details_url": f"/demo/{call_id}/details",
            "preview": False,
            "embed": False,
            "targets": _targets(),
        },
    )


@demo.get("/demo/{call_id}/details", response_class=HTMLResponse)
async def demo_dashboard(request: Request, call_id: str, registry: Registry) -> HTMLResponse:
    call = _call_or_404(registry, call_id)
    sides = [{"side": s, **join._side_view(call.get(f"{s}_lang") or "")} for s in ("a", "b")]
    return templates.TemplateResponse(
        request,
        "demo.html",
        {
            "call": call,
            "sides": sides,
            "turns_json": json.dumps(_turns(call), ensure_ascii=False, default=str),
            "events_url": f"/console/events?call={call_id}",
            "preview": False,
            "targets": _targets(),
        },
    )


def _summariser(request: Request) -> Any:
    """app.state.summarise (tests) or Claude with the configured key; None without one."""
    state = request.app.state
    if getattr(state, "summarise", None) is not None:
        return state.summarise
    settings = state.settings
    if not settings.anthropic_api_key:
        return None
    if getattr(state, "anthropic_client", None) is None:
        import anthropic

        state.anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    client, model = state.anthropic_client, settings.anthropic_model

    async def run(turns, story, langs):
        return await outcomes.summarise(client, model, turns, story, langs)

    return run


async def outcome_for(
    request: Request,
    call_id: str,
    call: dict[str, Any],
    story: dict[str, Any] | None,
    story_key: str | None,
) -> dict[str, Any] | None:
    """The outcome card's summary, cached per call, story and turn count."""
    run = _summariser(request)
    if run is None:
        return None
    turns = _turns(call)
    cache: dict = request.app.state.__dict__.setdefault("_outcomes", {})
    key = (call_id, story_key if story else None, len(turns))
    if key not in cache:
        langs = {s: call.get(f"{s}_lang") or "" for s in ("a", "b")}
        cache[key] = await run(turns, story, langs)
    return cache[key]


@demo.post("/demo/{call_id}/outcome")
async def outcome(
    request: Request, call_id: str, registry: Registry, story: str | None = None
) -> JSONResponse:
    call = _call_or_404(registry, call_id)
    found = stories.story(story)
    return JSONResponse({"outcome": await outcome_for(request, call_id, call, found, story)})
