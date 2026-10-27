"""The solo demo: a visitor talks to a worker who speaks only another language, played by
Claude (demo/agent.py), through the real pipeline. Public, so it is capped (settings.try_*).

  GET  /try                      the stories (demo/stories.py SOLO_ORDER), each with a "you
                                 speak" picker (public)
  POST /try                      form {story, lang} -> a loopback call with the agent on its
                                 side -> 303 /try/{call_id}?story=&t=<the visitor's token>
  GET  /try/{call_id}?story=&t=  the call: both phones, live transcripts, the visitor's mic
  POST /try/{call_id}/outcome?t= what the two agreed (as POST /demo/{call_id}/outcome)
  WS   /ws/try/{call_id}/monitor?story=&t=
                                 the agent's phone, for "hear both phones": binary b"S"+PCM
                                 (what it says) and b"H"+PCM (what it hears), PCM16 16 kHz;
                                 {"type":"clear"} when what it hears is cut short

The page streams the mic over /ws/loopback and reads the call's events from
/join/{call_id}/{side}/events, both with the join token (join/routes.py, api/loopback.py).
The agent's side is attached at once; the call goes live when the visitor taps start.

The pair keeps both sides in their own script: a pairs.yaml entry whose two sides use
Sarvam STT (not sarvam-translate, which shows Hindi speech as English), else one built
from the two languages' defaults.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import time
from dataclasses import replace
from typing import Annotated, Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.requests import HTTPConnection

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
from convox.config.pairs import PIVOT_STT, PairConfig, default_side, language_sides, orient
from convox.config.settings import Settings
from convox.demo import agent as agents
from convox.demo import stories
from convox.events import EventBus
from convox.interp.langid import SUPPORTED_LANGS
from convox.join import routes as join
from convox.join.tokens import make_join_token, verify_join_token
from convox.providers.registry import build_tts
from convox.types import Side, other

log = logging.getLogger(__name__)


def require_try(conn: HTTPConnection) -> None:
    if not conn.app.state.settings.try_enabled:
        raise HTTPException(404, "not found")


router = APIRouter(dependencies=[Depends(require_enabled), Depends(require_try)])

Book = Annotated[CallBook, Depends(get_call_book)]
Sessions = Annotated[Any, Depends(get_session_registry)]
Factory = Annotated[Any, Depends(get_session_factory)]
Pairs = Annotated[dict[str, PairConfig], Depends(get_pairs)]
Registry = Annotated[CallRegistry, Depends(get_registry)]
Bus = Annotated[EventBus, Depends(get_bus)]

DEFAULT_STORY = "auto"
DEFAULT_LANG = "hi-IN"  # the visitor's language unless they pick another
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


class Monitor:
    """The agent's phone audio for the pages listening in; a slow page loses audio."""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue[tuple[str, bytes]]] = set()
        self._spoke = False

    def publish(self, kind: str, pcm: bytes) -> None:
        # what the agent hears before its first line is the opening prompt, which the
        # visitor is hearing in their own language at the same moment: skip it
        self._spoke = self._spoke or kind == "said"
        if not self._spoke:
            return
        for q in list(self._subs):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait((kind, pcm))

    def subscribe(self) -> asyncio.Queue[tuple[str, bytes]]:
        q: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue(2000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[tuple[str, bytes]]) -> None:
        self._subs.discard(q)


def get_gate(request: Request) -> Gate:
    state = request.app.state
    if getattr(state, "try_gate", None) is None:
        state.try_gate = Gate()
    return state.try_gate


def monitors(app: Any) -> dict[str, Monitor]:
    if getattr(app.state, "try_monitors", None) is None:
        app.state.try_monitors = {}
    return app.state.try_monitors


def client_ip(request: Request) -> str:
    """The proxy (Traefik) appends the real client to X-Forwarded-For: take the last hop."""
    fwd = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in fwd.split(",") if h.strip()]
    if hops:
        return hops[-1]
    return request.client.host if request.client else "unknown"


def visitor_langs(agent_lang: str) -> list[str]:
    """Every language Convox interprets, except the agent's; the default first."""
    langs = [lang for lang in SUPPORTED_LANGS if lang != agent_lang]
    return sorted(langs, key=lambda x: (x != DEFAULT_LANG, SUPPORTED_LANGS.index(x)))


def solo_pair(pairs: dict[str, PairConfig], langs: dict[Side, str]) -> PairConfig:
    """A pair with both sides in their own script: a configured one whose sides both use
    native STT, else one from the languages' defaults with native STT and default knobs."""
    for p in pairs.values():
        if "fake" in p.key:
            continue
        o = orient(p, langs["a"], langs["b"])
        if o is not None and PIVOT_STT not in (o.a.stt, o.b.stt):
            return o
    sides = language_sides(pairs)

    def native(lang: str) -> Any:
        return replace(sides.get(lang) or default_side(lang), stt="sarvam", mt_src=None)

    key = f"{langs['a'].split('-')[0]}-{langs['b'].split('-')[0]}-solo"
    return PairConfig(key=key, a=native(langs["a"]), b=native(langs["b"]))


def solo_story(key: str, visitor_lang: str) -> tuple[dict[str, Any], Side]:
    """The story as the call page shows it: the visitor is "You", in their language."""
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


def _picker(request: Request, error: str | None, status: int, chosen: str | None = None):
    cards = []
    for key in stories.SOLO_ORDER:
        found = stories.solo(key)
        if found is None:
            continue
        side = found["agent"]["side"]
        agent = found[side]
        cards.append(
            {
                "key": key,
                "story": found,
                "stakes": found["agent"].get("solo_stakes") or found["stakes"],
                "agent": {**agent, **join._side_view(agent["lang"])},
                "you": found[other(side)],
                "langs": [join._side_view(lang) for lang in visitor_langs(agent["lang"])],
                "error": error if key == chosen else None,
            }
        )
    return templates.TemplateResponse(
        request,
        "try.html",
        {"cards": cards, "error": error if chosen is None else None},
        status_code=status,
    )


@router.get("/try", response_class=HTMLResponse)
async def try_picker(request: Request) -> HTMLResponse:
    return _picker(request, None, 200)


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
        return _picker(request, "Pick a story.", 400)
    agent_side: Side = found["agent"]["side"]
    agent_lang = found[agent_side]["lang"]
    if lang not in visitor_langs(agent_lang):
        return _picker(request, "Pick the language you speak.", 400, key)
    ip = client_ip(request)
    refused = gate.admit(settings, ip)
    if refused:
        return _picker(request, refused, 429, key)
    langs: dict[Side, str] = {agent_side: agent_lang, other(agent_side): lang}
    # record=False: no text in the database; the page reads it from the live events
    req = LoopbackCallRequest(
        a_lang=langs["a"], b_lang=langs["b"], record=False, vertical=found["vertical"]
    )
    try:
        out = create_loopback_call(
            request, req, book=book, sessions=sessions, factory=factory, pairs=pairs,
            co_located=False, pair_config=solo_pair(pairs, langs),
        )  # fmt: skip
    except ApiError as e:
        return _picker(request, e.message, e.status, key)
    session = sessions.get(out.call_id)
    gate.start(ip, out.call_id)
    monitor = monitors(request.app)[out.call_id] = Monitor()
    leg = agents.AgentLeg(agent_side, tap=monitor.publish)
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
    app = request.app
    settings: Settings = app.state.settings
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
        monitors(app).pop(session.call_id, None)
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


def _visitor(settings: Settings, call_id: str, story: str | None, t: str | None):
    found = stories.solo(story or DEFAULT_STORY)
    if found is None:
        raise HTTPException(404, "no such story")
    agent_side: Side = found["agent"]["side"]
    side = other(agent_side)
    if not verify_join_token(settings, t, call_id, side):
        raise HTTPException(403, "this link has expired")
    return story or DEFAULT_STORY, agent_side, side


@router.get("/try/{call_id}", response_class=HTMLResponse)
async def try_call(
    request: Request,
    call_id: str,
    registry: Registry,
    sessions: Sessions,
    story: str | None = None,
    t: str | None = None,
) -> Response:
    settings: Settings = request.app.state.settings
    key, agent_side, side = _visitor(settings, call_id, story, t)
    call = registry.get_call(call_id)
    if call is None or sessions.get(call_id) is None:
        return RedirectResponse("/try", status_code=303)
    langs = {s: call.get(f"{s}_lang") or "" for s in ("a", "b")}
    shown, _ = solo_story(key, langs[side])
    people = {}
    for s in ("a", "b"):
        who = shown[s]
        people[s] = {
            "side": s,
            "agent": s == agent_side,
            "role": who["role"],
            "name": who["name"] or "You",
            **join._side_view(langs[s]),
        }
    short = langs[side].split("-")[0]
    q = f"call={call_id}&side={side}&t={t}"
    return templates.TemplateResponse(
        request,
        "try_call.html",
        {
            "call_id": call_id,
            "story_key": key,
            "story": shown,
            "people": people,
            "me": people[side],
            "agent": people[agent_side],
            "side": side,
            "agent_side": agent_side,
            "ws_path": f"/ws/loopback?{q}",
            "monitor_path": f"/ws/try/{call_id}/monitor?story={key}&t={t}",
            "events_url": f"/join/{call_id}/{side}/events?t={t}",
            "outcome_url": f"/try/{call_id}/outcome?story={key}&t={t}",
            "ui": join.UI.get(short, join.UI["en"]),
            "max_s": settings.try_max_s,
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
    key, _, side = _visitor(request.app.state.settings, call_id, story, t)
    call = site._call_or_404(registry, call_id)
    shown, _ = solo_story(key, call.get(f"{side}_lang") or "")
    return JSONResponse({"outcome": await site.outcome_for(request, call_id, call, shown, key)})


@router.websocket("/ws/try/{call_id}/monitor")
async def try_monitor(
    ws: WebSocket, call_id: str, story: str | None = None, t: str | None = None
) -> None:
    await ws.accept()
    try:
        _visitor(ws.app.state.settings, call_id, story, t)
    except HTTPException:
        await ws.close(4401)
        return
    monitor = monitors(ws.app).get(call_id)
    if monitor is None:
        await ws.close(4404)
        return
    q = monitor.subscribe()
    reader = asyncio.create_task(_drain(ws))
    try:
        while not reader.done():
            get = asyncio.ensure_future(q.get())
            done, _ = await asyncio.wait({get, reader}, return_when=asyncio.FIRST_COMPLETED)
            if get not in done:
                get.cancel()
                break
            kind, pcm = get.result()
            if kind == "clear":
                await ws.send_json({"type": "clear"})
            else:
                await ws.send_bytes((b"S" if kind == "said" else b"H") + pcm)
    except Exception:  # the page went away
        pass
    finally:
        monitor.unsubscribe(q)
        reader.cancel()
        with contextlib.suppress(BaseException):
            await reader
        with contextlib.suppress(Exception):
            await ws.close()


async def _drain(ws: WebSocket) -> None:
    """Read (and ignore) until the page closes the socket."""
    with contextlib.suppress(Exception):
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                return
