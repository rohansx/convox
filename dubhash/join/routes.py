"""Stage demo without telephony: two phones join a loopback call by scanning QR codes.

  GET  /join                        host page (console auth): pick a pair, "Start call"
  POST /join                        form {a_lang, b_lang} -> loopback call -> 303 /join/{id}
  GET  /join/{call_id}              host view (console auth): two QR codes, one per side,
                                    and the call's live transcript (console SSE)
  GET  /join/{call_id}/{side}?t=    the phone page (join token, no password)
  GET  /join/{call_id}/{side}/events?t=
                                    the call's events for the phone (SSE, join token)

The phone streams its mic over /ws/loopback?call=&side=&t= (api/loopback.py), which takes
the same token. The QR codes encode settings.public_base_url (the tunnel or VM host), not
the host the laptop used to open this page: a laptop on localhost still hands phones a
reachable https URL. Phones need https for the mic (only localhost is exempt).
"""

from __future__ import annotations

import html
import json
from typing import Annotated, Any
from urllib.parse import parse_qsl

import segno
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from dubhash.api.calls import CallBook, get_call_book, get_pairs
from dubhash.api.console import TARGET_P50_MS, TARGET_P95_MS, TEMPLATES_DIR
from dubhash.api.deps import (
    CallRegistry,
    get_bus,
    get_registry,
    get_session_factory,
    get_session_registry,
    require_console_auth,
)
from dubhash.api.loopback import create_loopback_call, require_enabled
from dubhash.api.schemas import ApiError, LoopbackCallRequest, validation_error
from dubhash.config.pairs import AUTO, PairConfig, find_pair, language_sides
from dubhash.config.settings import Settings
from dubhash.console.sse import sse_stream
from dubhash.events import EventBus
from dubhash.interp.langid import SUPPORTED_LANGS
from dubhash.join.tokens import make_join_token, verify_join_token
from dubhash.ward.service import LANGS

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

host = APIRouter(dependencies=[Depends(require_enabled), Depends(require_console_auth)])
phone = APIRouter(dependencies=[Depends(require_enabled)])

Book = Annotated[CallBook, Depends(get_call_book)]
Sessions = Annotated[Any, Depends(get_session_registry)]
Factory = Annotated[Any, Depends(get_session_factory)]
Pairs = Annotated[dict[str, PairConfig], Depends(get_pairs)]
Registry = Annotated[CallRegistry, Depends(get_registry)]

# Stage order: the user's pair first, then the rest of the demo languages.
PREFERRED = ("mr-IN", "hi-IN", "en-IN", "gu-IN", "kn-IN", "ta-IN")

# Phone UI strings in the phone's own language, with English underneath on the page.
# Kannada, Tamil, Marathi and Gujarati strings were written by us: they need a native
# speaker's check.
UI: dict[str, dict[str, str]] = {
    "en": {"start": "Tap to start", "listening": "Listening", "speaking": "Speaking",
           "leave": "Leave call", "ended": "Call ended", "waiting": "Waiting for the other phone",
           "connecting": "Connecting", "you": "You", "resume": "Tap to resume"},
    "hi": {"start": "शुरू करने के लिए टैप करें", "listening": "सुन रहा है", "speaking": "बोल रहा है",
           "leave": "कॉल छोड़ें", "ended": "कॉल खत्म", "waiting": "दूसरे फ़ोन का इंतज़ार",
           "connecting": "जुड़ रहा है", "you": "आप", "resume": "फिर शुरू करने के लिए टैप करें"},
    "mr": {"start": "सुरू करण्यासाठी टॅप करा", "listening": "ऐकत आहे", "speaking": "बोलत आहे",
           "leave": "कॉल सोडा", "ended": "कॉल संपला", "waiting": "दुसऱ्या फोनची वाट पाहत आहे",
           "connecting": "जोडत आहे", "you": "तुम्ही", "resume": "पुन्हा सुरू करण्यासाठी टॅप करा"},
    "kn": {"start": "ಪ್ರಾರಂಭಿಸಲು ಟ್ಯಾಪ್ ಮಾಡಿ", "listening": "ಕೇಳುತ್ತಿದೆ", "speaking": "ಮಾತನಾಡುತ್ತಿದೆ",
           "leave": "ಕರೆ ಬಿಡಿ", "ended": "ಕರೆ ಮುಗಿದಿದೆ", "waiting": "ಇನ್ನೊಂದು ಫೋನ್‌ಗಾಗಿ ಕಾಯುತ್ತಿದೆ",
           "connecting": "ಸಂಪರ್ಕಿಸುತ್ತಿದೆ", "you": "ನೀವು", "resume": "ಮುಂದುವರಿಸಲು ಟ್ಯಾಪ್ ಮಾಡಿ"},
    "ta": {"start": "தொடங்க தட்டவும்", "listening": "கேட்கிறது", "speaking": "பேசுகிறது",
           "leave": "அழைப்பை விடு", "ended": "அழைப்பு முடிந்தது",
           "waiting": "மற்ற போனுக்காகக் காத்திருக்கிறது", "connecting": "இணைக்கிறது",
           "you": "நீங்கள்", "resume": "தொடர தட்டவும்"},
    # TODO(PQ5): native Gujarati review.
    "gu": {"start": "શરૂ કરવા માટે ટૅપ કરો", "listening": "સાંભળી રહ્યું છે", "speaking": "બોલી રહ્યું છે",
           "leave": "કૉલ છોડો", "ended": "કૉલ પૂરો થયો", "waiting": "બીજા ફોનની રાહ જોવાઈ રહી છે",
           "connecting": "જોડાઈ રહ્યું છે", "you": "તમે", "resume": "ફરી શરૂ કરવા માટે ટૅપ કરો"},
}  # fmt: skip


# lang "auto": the side's language is detected on the call; until then the phone shows a
# neutral "speak in your language". TODO(PQ5): native review.
AUTO_VIEW = {"lang": AUTO, "native": "अपनी भाषा में बोलिए", "english": "Speak in your language"}
AUTO_LABEL = ("Auto-detect", "अपनी भाषा")


def native(lang: str) -> str:
    if lang == AUTO:
        return AUTO_LABEL[1]
    return LANGS.get(lang, (lang, lang))[1]


def english(lang: str) -> str:
    if lang == AUTO:
        return AUTO_LABEL[0]
    return LANGS.get(lang, (lang, lang))[0]


def offered_pairs(pairs: dict[str, PairConfig]) -> list[tuple[str, str]]:
    """(a_lang, b_lang) combinations find_pair resolves, once each, stage pair first."""
    langs = {p.side(s).lang for p in pairs.values() if "fake" not in p.key for s in ("a", "b")}
    out = {(a, b) for a in langs for b in langs if a != b and find_pair(pairs, a, b)}

    def rank(lang: str) -> int:
        return PREFERRED.index(lang) if lang in PREFERRED else len(PREFERRED)

    return sorted(out, key=lambda ab: (rank(ab[0]), rank(ab[1]), ab))


def phone_url(settings: Settings, call_id: str, side: str) -> str:
    token = make_join_token(settings, call_id, side)
    return f"{settings.public_base_url.rstrip('/')}/join/{call_id}/{side}?t={token}"


def qr_svg(url: str) -> str:
    """Inline SVG (no XML declaration, no CDN). Error level M survives a glare spot on a
    projector; the 4-module quiet zone is the spec's and keeps phones locking on fast."""
    return segno.make(url, error="m", micro=False).svg_inline(
        scale=1, border=4, dark="#111111", light="#ffffff", omitsize=True
    )


def _side_view(lang: str) -> dict[str, str]:
    return {"lang": lang, "native": native(lang), "english": english(lang)}


def _pair_views(pairs: dict[str, PairConfig]) -> list[dict[str, Any]]:
    return [
        {"a": _side_view(a), "b": _side_view(b), "value": f"{a}|{b}"}
        for a, b in offered_pairs(pairs)
    ]


def side_langs(pairs: dict[str, PairConfig]) -> list[str]:
    """Languages one side can be fixed to (the per-side picker), stage order."""
    known = language_sides(pairs)
    langs = [lang for lang in SUPPORTED_LANGS if lang in known]
    return sorted(langs, key=lambda x: PREFERRED.index(x) if x in PREFERRED else len(PREFERRED))


def _picker(request: Request, pairs: dict[str, PairConfig], error: str | None, status: int):
    return templates.TemplateResponse(
        request,
        "join_host.html",
        {
            "call": None,
            "pairs": _pair_views(pairs),
            "side_langs": [_side_view(lang) for lang in side_langs(pairs)],
            "error": error,
        },
        status_code=status,
    )


@host.get("/join", response_class=HTMLResponse)
async def join_picker(request: Request, pairs: Pairs) -> HTMLResponse:
    return _picker(request, pairs, None, 200)


@host.post("/join", response_model=None)
async def join_start(
    request: Request, book: Book, sessions: Sessions, factory: Factory, pairs: Pairs
) -> Response:
    form = dict(parse_qsl((await request.body()).decode(errors="replace")))
    if "pair" in form and "a_lang" not in form:  # the picker posts "a|b"
        form["a_lang"], _, form["b_lang"] = form.pop("pair").partition("|")
    form.pop("pair", None)
    form["record"] = form.get("record", "true")  # the stage's turn log keeps text (step 5)
    try:
        req = LoopbackCallRequest.model_validate(form)
        out = create_loopback_call(
            request, req, book=book, sessions=sessions, factory=factory, pairs=pairs
        )
    except ValidationError as e:
        return _picker(request, pairs, validation_error(e).message, 400)
    except ApiError as e:
        return _picker(request, pairs, e.message, e.status)
    return RedirectResponse(f"/join/{out.call_id}", status_code=303)


@host.get("/join/{call_id}", response_class=HTMLResponse)
async def join_host_view(request: Request, call_id: str, registry: Registry) -> HTMLResponse:
    call = registry.get_call(call_id)
    if call is None:
        raise HTTPException(404, "no such call")
    settings: Settings = request.app.state.settings
    sides = []
    for side in ("a", "b"):
        lang = call.get(f"{side}_lang") or ""
        url = phone_url(settings, call_id, side)
        sides.append({"side": side, **_side_view(lang), "url": url, "svg": qr_svg(url)})
    turns = [t for t in call.get("turns", []) if not t.get("backchannel")]
    return templates.TemplateResponse(
        request,
        "join_host.html",
        {
            "call": call,
            "sides": sides,
            "turns_json": json.dumps(turns, ensure_ascii=False, default=str),
            "events_url": f"/console/events?call={call_id}",
            "targets": {"p50": TARGET_P50_MS, "p95": TARGET_P95_MS},
            "pairs": [],
            "error": None,
        },
    )


# --- phone ------------------------------------------------------------------------------


def _phone_session(request: Request, sessions: Any, call_id: str, side: str, token: str | None):
    if side not in ("a", "b"):
        raise HTTPException(404, "no such side")
    if not verify_join_token(request.app.state.settings, token, call_id, side):
        raise HTTPException(403, "this link has expired or is not for this call")
    session = sessions.get(call_id)
    if session is None:
        raise HTTPException(404, "call ended")
    return session


def _ended_page() -> HTMLResponse:
    body = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Call ended</title><link rel='stylesheet' href='/static/console/join.css'>"
        "</head><body class='phone'><main class='phone-main'><div class='phone-ended'>"
        "<h1>Call ended</h1><p>Scan a new code on the screen to join again.</p>"
        "</div></main></body></html>"
    )
    return HTMLResponse(body, status_code=404, headers={"cache-control": "no-store"})


def _detected(session: Any, book: CallBook, call_id: str, side: str) -> str:
    """The side's language for the page: its fixed language, or for lang "auto" the one
    detected so far, else AUTO."""
    handle = book.get(call_id)
    if handle is None or handle.legs[side].lang != AUTO:  # type: ignore[index]
        return str(session.pair.side(side).lang)
    states = getattr(session, "lang_states", None)
    state = states().get(side, {}) if callable(states) else {}
    lang = state.get("lang")
    return str(lang) if lang and state.get("state") == "CONFIRMED" else AUTO


@phone.get("/join/{call_id}/{side}", response_class=HTMLResponse)
async def join_phone(
    request: Request,
    call_id: str,
    side: str,
    sessions: Sessions,
    book: Book,
    t: str | None = None,
) -> HTMLResponse:
    try:
        session = _phone_session(request, sessions, call_id, side, t)
    except HTTPException as e:
        if e.status_code == 404 and e.detail == "call ended":
            return _ended_page()
        if e.status_code == 403:
            return HTMLResponse(
                f"<!doctype html><meta charset='utf-8'><title>Link expired</title>"
                f"<p>{html.escape(str(e.detail))}</p>",
                status_code=403,
                headers={"cache-control": "no-store"},
            )
        raise
    lang = _detected(session, book, call_id, side)
    other_lang = _detected(session, book, call_id, "b" if side == "a" else "a")
    short = "hi" if lang == AUTO else lang.split("-")[0]
    me = AUTO_VIEW if lang == AUTO else _side_view(lang)
    names = {code: list(LANGS[code]) for code in SUPPORTED_LANGS if code in LANGS}
    q = f"call={call_id}&side={side}&t={t}"
    return templates.TemplateResponse(
        request,
        "join_phone.html",
        {
            "call_id": call_id,
            "side": side,
            "me": me,
            "names": names,
            "other": _side_view(other_lang),
            "short": short,
            "ui": UI.get(short, UI["en"]),
            "ui_en": UI["en"],
            "ws_path": f"/ws/loopback?{q}",
            "events_url": f"/join/{call_id}/{side}/events?t={t}",
        },
        headers={"cache-control": "no-store"},
    )


@phone.get("/join/{call_id}/{side}/events")
async def join_phone_events(
    request: Request,
    call_id: str,
    side: str,
    sessions: Sessions,
    bus: Annotated[EventBus, Depends(get_bus)],
    t: str | None = None,
) -> StreamingResponse:
    _phone_session(request, sessions, call_id, side, t)
    return StreamingResponse(
        sse_stream(bus, call_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
