"""The solo demo: a visitor talks to someone who speaks only another language, played by
Claude (demo/agent.py), through the real pipeline. Public, so it is capped (settings.try_*).

  GET  /try                      pick your language for the story (public)
  POST /try                      form {lang, story} -> a loopback call with the agent on its
                                 side -> 303 /try/{call_id}?t=<the visitor's join token>
  GET  /try/{call_id}?t=         the live stage, with the visitor's mic in the page
  POST /try/{call_id}/outcome?t= what the two agreed (as POST /demo/{call_id}/outcome)

The page streams the mic over /ws/loopback and reads the call's events from
/join/{call_id}/{side}/events, both with the join token (join/routes.py, api/loopback.py).
The agent's side is attached at once; the call goes live when the visitor taps start.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import time
from typing import Annotated, Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from convox.api import site
from convox.api.calls import CallBook, get_call_book, get_pairs
from convox.api.console import templates
from convox.api.deps import (
    CallRegistry,
    get_bus,
    get_registry,
    get_session_factory,
    get_session_registry,
)
from convox.api.loopback import create_loopback_call, require_enabled
from convox.api.schemas import ApiError, LoopbackCallRequest
from convox.config.pairs import PairConfig, find_pair
from convox.config.settings import Settings
from convox.demo import agent as agents
from convox.demo import stories
from convox.events import EventBus
from convox.join import routes as join
from convox.join.tokens import make_join_token, verify_join_token
from convox.providers.registry import build_tts
from convox.types import Side, other

log = logging.getLogger(__name__)


def require_try(request: Request) -> None:
    if not request.app.state.settings.try_enabled:
        raise HTTPException(404, "not found")


router = APIRouter(dependencies=[Depends(require_enabled), Depends(require_try)])

Book = Annotated[CallBook, Depends(get_call_book)]
Sessions = Annotated[Any, Depends(get_session_registry)]
Factory = Annotated[Any, Depends(get_session_factory)]
Pairs = Annotated[dict[str, PairConfig], Depends(get_pairs)]
Registry = Annotated[CallRegistry, Depends(get_registry)]
Bus = Annotated[EventBus, Depends(get_bus)]

DEFAULT_STORY = "auto"
# The visitor's languages, offered in this order when a pair with the agent's exists.
VISITOR_LANGS = ("hi-IN", "en-IN", "ta-IN", "mr-IN", "gu-IN")
# The visitor sees their own words: where the default pair recognises their speech straight
# into English (sarvam-translate), use the direct pair, which keeps the original text.
DIRECT_PAIRS = {("kn-IN", "hi-IN"): "hi-kn-direct"}
DAY_S = 24 * 3600


class Gate:
    """Calls at once and calls per visitor per rolling day, in memory (one worker)."""

    def __init__(self) -> None:
        self.live: set[str] = set()
        self._starts: dict[str, list[float]] = {}

    def admit(self, settings: Settings, ip: str, now: float | None = None) -> str | None:
        """None if a call may start, else the reason it may not."""
        now = time.time() if now is None else now
        recent = [t for t in self._starts.get(ip, []) if now - t < DAY_S]
        self._starts[ip] = recent
        if len(self.live) >= settings.try_max_live:
            return "All our lines are busy right now. Try again in a few minutes."
        if len(recent) >= settings.try_per_ip_day:
            return "You've had a few calls today. Book a demo and we'll show you more."
        return None

    def start(self, ip: str, call_id: str, now: float | None = None) -> None:
        self._starts.setdefault(ip, []).append(time.time() if now is None else now)
        self.live.add(call_id)

    def done(self, call_id: str) -> None:
        self.live.discard(call_id)


def get_gate(request: Request) -> Gate:
    state = request.app.state
    if getattr(state, "try_gate", None) is None:
        state.try_gate = Gate()
    return state.try_gate


def client_ip(request: Request) -> str:
    """The proxy (Traefik) appends the real client to X-Forwarded-For: take the last hop."""
    fwd = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in fwd.split(",") if h.strip()]
    if hops:
        return hops[-1]
    return request.client.host if request.client else "unknown"


def visitor_langs(pairs: dict[str, PairConfig], agent_lang: str) -> list[str]:
    return [lang for lang in VISITOR_LANGS if find_pair(pairs, agent_lang, lang) is not None]


def solo_story(key: str, visitor_lang: str) -> tuple[dict[str, Any], Side]:
    """The story as the stage shows it solo: the visitor is "You", in their language."""
    found = stories.solo(key)
    if found is None:
        raise HTTPException(404, "no such story")
    s = copy.deepcopy(found)
    agent_side: Side = s["agent"]["side"]
    me = s[other(agent_side)]
    me.update({"name": "You", "lang": visitor_lang, "lines": []})
    s["stakes"] = s["agent"].get("solo_stakes") or s["stakes"]
    return s, agent_side


# --- pages ------------------------------------------------------------------------------


def _picker(request: Request, pairs: dict[str, PairConfig], error: str | None, status: int):
    story = stories.solo(DEFAULT_STORY)
    assert story is not None
    agent_side = story["agent"]["side"]
    agent_lang = story[agent_side]["lang"]
    return templates.TemplateResponse(
        request,
        "try.html",
        {
            "story_key": DEFAULT_STORY,
            "story": story,
            "agent": {**story[agent_side], **join._side_view(agent_lang)},
            "you": story[other(agent_side)],
            "langs": [join._side_view(lang) for lang in visitor_langs(pairs, agent_lang)],
            "error": error,
        },
        status_code=status,
    )


@router.get("/try", response_class=HTMLResponse)
async def try_picker(request: Request, pairs: Pairs) -> HTMLResponse:
    return _picker(request, pairs, None, 200)


@router.post("/try", response_model=None)
async def try_start(
    request: Request,
    book: Book,
    sessions: Sessions,
    factory: Factory,
    pairs: Pairs,
    bus: Bus,
    gate: Annotated[Gate, Depends(get_gate)],
) -> Response:
    settings: Settings = request.app.state.settings
    form = dict(parse_qsl((await request.body()).decode(errors="replace")))
    key = form.get("story") or DEFAULT_STORY
    lang = form.get("lang") or ""
    found = stories.solo(key)
    if found is None:
        return _picker(request, pairs, "Pick a story.", 400)
    agent_side: Side = found["agent"]["side"]
    agent_lang = found[agent_side]["lang"]
    if lang not in visitor_langs(pairs, agent_lang):
        return _picker(request, pairs, "Pick the language you speak.", 400)
    ip = client_ip(request)
    refused = gate.admit(settings, ip)
    if refused:
        return _picker(request, pairs, refused, 429)
    langs = {agent_side: agent_lang, other(agent_side): lang}
    # record=False: no text in the database; the stage reads it from the live events
    direct = DIRECT_PAIRS.get((agent_lang, lang))
    req = LoopbackCallRequest(
        a_lang=langs["a"],
        b_lang=langs["b"],
        record=False,
        vertical=found["vertical"],
        pair=direct if direct in pairs else None,
    )
    try:
        out = create_loopback_call(
            request, req, book=book, sessions=sessions, factory=factory, pairs=pairs,
            co_located=False,
        )  # fmt: skip
    except ApiError as e:
        return _picker(request, pairs, e.message, e.status)
    session = sessions.get(out.call_id)
    gate.start(ip, out.call_id)
    leg = agents.AgentLeg(agent_side)
    book.leg_attached(out.call_id, agent_side)
    await session.attach_leg(agent_side, leg)
    book.spawn(_run(request, session, bus, gate, found, agent_side, leg))
    log.info("try.start", extra={"call_id": out.call_id, "story": key, "lang": lang})
    token = make_join_token(settings, out.call_id, other(agent_side))
    return RedirectResponse(f"/try/{out.call_id}?story={key}&t={token}", status_code=303)


async def _run(
    request: Request,
    session: Any,
    bus: EventBus,
    gate: Gate,
    story: dict[str, Any],
    side: Side,
    leg: agents.AgentLeg,
) -> None:
    """The agent for one call, inside the call's time limits; always frees the gate."""
    settings: Settings = request.app.state.settings
    cfg = session.pair.side(side)
    tts = build_tts(cfg.tts, settings)
    voice = cfg.voice("m" if story[side].get("gender", "m") == "m" else "f")

    async def speak(text: str) -> bytes:
        return await agents.synthesise(tts, text, voice, cfg.lang)

    agent = agents.Agent(
        session,
        bus,
        side,
        leg,
        reply=_reply(request, story, side, session.pair.side(other(side)).lang),
        speak=speak,
        opener=story["agent"]["opener"],
        nudge=story["agent"].get("nudge"),
    )
    task = asyncio.create_task(agent.run(), name=f"{session.call_id}-agent")
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.try_start_s
        # the session has no "active" event to await; ringing ends when the visitor taps start
        while session.status == "ringing" and loop.time() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.25)
        if session.status == "ringing":
            await session.hangup("no_answer")
            return
        try:
            await asyncio.wait_for(session.wait_closed(), settings.try_max_s)
        except TimeoutError:
            log.info("try.time_limit", extra={"call_id": session.call_id})
            await session.hangup("time_limit")
    finally:
        gate.done(session.call_id)
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        aclose = getattr(tts, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


def _reply(request: Request, story: dict[str, Any], side: Side, them_lang: str) -> agents.Reply:
    """Claude in the story's role, app.state.agent_reply in tests, else the story's lines."""
    state = request.app.state
    if getattr(state, "agent_reply", None) is not None:
        return state.agent_reply
    settings: Settings = state.settings
    if not settings.anthropic_api_key:
        return agents.scripted(story[side]["lines"][1:])
    if getattr(state, "anthropic_client", None) is None:
        import anthropic

        state.anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    langs = (join.english(story[side]["lang"]), join.english(them_lang))
    return agents.claude_reply(state.anthropic_client, settings.anthropic_model, story, side, langs)


def _visitor(request: Request, call_id: str, story: str | None, t: str | None):
    found = stories.solo(story or DEFAULT_STORY)
    if found is None:
        raise HTTPException(404, "no such story")
    agent_side: Side = found["agent"]["side"]
    side = other(agent_side)
    if not verify_join_token(request.app.state.settings, t, call_id, side):
        raise HTTPException(403, "this link has expired")
    return story or DEFAULT_STORY, agent_side, side


@router.get("/try/{call_id}", response_class=HTMLResponse)
async def try_stage(
    request: Request,
    call_id: str,
    registry: Registry,
    sessions: Sessions,
    story: str | None = None,
    t: str | None = None,
) -> Response:
    key, agent_side, side = _visitor(request, call_id, story, t)
    call = registry.get_call(call_id)
    if call is None or sessions.get(call_id) is None:
        return RedirectResponse("/try", status_code=303)
    langs = {s: call.get(f"{s}_lang") or "" for s in ("a", "b")}
    shown, _ = solo_story(key, langs[side])
    sides = site._stage_sides(shown, langs, {})
    for s in sides:
        s["agent"] = s["side"] == agent_side
    me = langs[side]
    short = me.split("-")[0]
    q = f"call={call_id}&side={side}&t={t}"
    return templates.TemplateResponse(
        request,
        "stage.html",
        {
            "call": call,
            "story_key": key,
            "story": shown,
            "sides": sides,
            "turns_json": json.dumps(site._turns(call), ensure_ascii=False, default=str),
            "events_url": f"/join/{call_id}/{side}/events?t={t}",
            "outcome_url": f"/try/{call_id}/outcome?story={key}&t={t}",
            "details_url": None,
            "preview": False,
            "embed": False,
            "targets": site._targets(),
            "solo": {
                "side": side,
                "agent_side": agent_side,
                "me": join._side_view(me),
                "agent": shown[agent_side],
                "ws_path": f"/ws/loopback?{q}",
                "ui": join.UI.get(short, join.UI["en"]),
                "ui_en": join.UI["en"],
                "max_s": request.app.state.settings.try_max_s,
            },
        },
        headers={"cache-control": "no-store"},
    )


@router.post("/try/{call_id}/outcome")
async def try_outcome(
    request: Request,
    call_id: str,
    registry: Registry,
    story: str | None = None,
    t: str | None = None,
) -> JSONResponse:
    key, _, side = _visitor(request, call_id, story, t)
    call = site._call_or_404(registry, call_id)
    shown, _ = solo_story(key, call.get(f"{side}_lang") or "")
    return JSONResponse({"outcome": await site.outcome_for(request, call_id, call, shown, key)})
