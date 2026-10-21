"""Loopback: the no-telephony dev path and last-resort demo (leg-transports.md section 5).

  GET  /loopback                  the browser leg page (dubhash/legs/static/loopback.html)
  POST /loopback/calls            {a_lang, b_lang, mode, record?, pair?}
                                  -> 201 {call_id, a_url, b_url}; `pair` names a
                                  pairs.yaml key (e.g. hi-kn-direct), `record` keeps text
  GET  /loopback/new              tiny HTML form; POST /loopback/new creates a call and
                                  shows the two links (open one per tab or device)
  WS   /ws/loopback?call=&side=   the page's socket, wrapped in WebRtcLeg and attached
                                  &t=<join token> (dubhash/join): a phone's auth; a bad
                                  or foreign token closes with 4401

Nothing is dialed and the no-answer rule does not apply (people need time to open tabs).
Disabled (404) when ENABLE_WEBRTC_LEG is false. Behind the console's auth (off in test):
loopback pages carry live audio and are not for the open internet.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qsl, quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import ValidationError
from starlette.requests import HTTPConnection

from dubhash.api.calls import (
    SIDES,
    CallBook,
    CallHandle,
    LegState,
    get_call_book,
    get_pairs,
    new_call_id,
    note_console,
    unsupported_pair,
)
from dubhash.api.deps import get_session_factory, get_session_registry, require_console_auth
from dubhash.api.legs_ws import (
    CLOSE_BAD_REQUEST,
    CLOSE_CONFLICT,
    CLOSE_UNAUTHORIZED,
    CLOSE_UNKNOWN_CALL,
    close_ws,
    serve_leg,
)
from dubhash.api.schemas import (
    ApiError,
    LoopbackCallRequest,
    LoopbackCallResponse,
    validation_error,
)
from dubhash.config.pairs import PairConfig, find_pair, orient
from dubhash.config.settings import Settings
from dubhash.join.tokens import verify_join_token
from dubhash.legs.webrtc import WebRtcLeg

PAGE = Path(__file__).resolve().parent.parent / "legs" / "static" / "loopback.html"


def require_enabled(conn: HTTPConnection) -> None:
    if not conn.app.state.settings.enable_webrtc_leg:
        raise HTTPException(404, "loopback disabled")


router = APIRouter(dependencies=[Depends(require_enabled)])
http = APIRouter(dependencies=[Depends(require_enabled), Depends(require_console_auth)])

Book = Annotated[CallBook, Depends(get_call_book)]
Sessions = Annotated[Any, Depends(get_session_registry)]
Factory = Annotated[Any, Depends(get_session_factory)]
Pairs = Annotated[dict[str, PairConfig], Depends(get_pairs)]


def loopback_ws_url(settings: Settings, call_id: str, side: str) -> str:
    return f"{settings.edge_wss_base.rstrip('/')}/ws/loopback?call={call_id}&side={side}"


def page_url(settings: Settings, call_id: str, side: str, lang: str) -> str:
    query = urlencode({"ws": loopback_ws_url(settings, call_id, side), "side": side, "lang": lang})
    return f"{settings.public_base_url.rstrip('/')}/loopback?{query}"


def create_loopback_call(
    request: Request,
    req: LoopbackCallRequest,
    *,
    book: CallBook,
    sessions: Any,
    factory: Any,
    pairs: dict[str, PairConfig],
) -> LoopbackCallResponse:
    settings: Settings = request.app.state.settings
    if req.pair is not None:
        named = pairs.get(req.pair)
        pair = orient(named, req.a_lang, req.b_lang) if named is not None else None
        if pair is None:
            raise ApiError(
                422, "unsupported_pair", f"no pair {req.pair!r} for {req.a_lang}-{req.b_lang}"
            )
    else:
        pair = find_pair(pairs, req.a_lang, req.b_lang)
    if pair is None:
        raise unsupported_pair(req.a_lang, req.b_lang)
    call_id = new_call_id()
    # both phones in one room (/join, loopback): cross-talk gate on, no ducked original
    session = factory(call_id, pair, mode=req.mode, record=req.record, co_located=True)
    sessions.create(call_id, session)
    handle = CallHandle(
        call_id=call_id,
        session=session,
        provider="webrtc",
        mode=req.mode,
        record=req.record,
        legs={"a": LegState(req.a_lang), "b": LegState(req.b_lang)},
    )
    book.add(handle)
    note_console(
        request,
        {
            "call_id": call_id,
            "a_lang": req.a_lang,
            "b_lang": req.b_lang,
            "provider": "webrtc",
            "mode": req.mode,
            "status": "ringing",
        },
    )
    book.spawn(book.supervise(handle, sessions, no_answer=False))
    return LoopbackCallResponse(
        call_id=call_id,
        a_url=page_url(settings, call_id, "a", req.a_lang),
        b_url=page_url(settings, call_id, "b", req.b_lang),
    )


@http.get("/loopback")
async def loopback_page() -> FileResponse:
    return FileResponse(PAGE, media_type="text/html")


@http.post("/loopback/calls", status_code=201)
async def loopback_calls(
    request: Request, book: Book, sessions: Sessions, factory: Factory, pairs: Pairs
) -> JSONResponse:
    try:
        req = LoopbackCallRequest.model_validate(await request.json())
    except ValueError as e:
        if isinstance(e, ValidationError):
            raise validation_error(e) from None
        raise ApiError(400, "invalid_body", "body is not valid JSON") from None
    out = create_loopback_call(
        request, req, book=book, sessions=sessions, factory=factory, pairs=pairs
    )
    return JSONResponse(out.model_dump(), status_code=201)


def _langs(pairs: dict[str, PairConfig]) -> list[str]:
    seen = {p.side(s).lang for p in pairs.values() if "fake" not in p.key for s in SIDES}
    return sorted(seen)


def _shell(body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>dubhash loopback</title><style>body{font-family:system-ui,sans-serif;"
        "max-width:40rem;margin:2rem auto;padding:0 1rem;line-height:1.5}"
        "a{word-break:break-all}label{display:block;margin:.5rem 0}</style></head>"
        f"<body><h1>dubhash loopback</h1>{body}</body></html>"
    )


def _form(langs: list[str], error: str | None = None) -> str:
    def select(name: str, default: str) -> str:
        opts = "".join(
            f'<option value="{html.escape(lang)}"{" selected" if lang == default else ""}>'
            f"{html.escape(lang)}</option>"
            for lang in langs
        )
        return f'<select name="{name}">{opts}</select>'

    err = f"<p role='alert'>{html.escape(error)}</p>" if error else ""
    return _shell(
        f"{err}<form method='post' action='/loopback/new'>"
        f"<label>Side A speaks {select('a_lang', 'hi-IN')}</label>"
        f"<label>Side B speaks {select('b_lang', 'kn-IN')}</label>"
        "<label>Mode <select name='mode'><option>interpret</option><option>bridge</option>"
        "</select></label><button type='submit'>Create call</button></form>"
    )


@http.get("/loopback/new", response_class=HTMLResponse)
async def loopback_new_form(pairs: Pairs) -> HTMLResponse:
    return HTMLResponse(_form(_langs(pairs)))


@http.post("/loopback/new", response_class=HTMLResponse)
async def loopback_new_submit(
    request: Request, book: Book, sessions: Sessions, factory: Factory, pairs: Pairs
) -> HTMLResponse:
    form = dict(parse_qsl((await request.body()).decode(errors="replace")))
    try:
        req = LoopbackCallRequest.model_validate(form)
        out = create_loopback_call(
            request, req, book=book, sessions=sessions, factory=factory, pairs=pairs
        )
    except ValidationError as e:
        return HTMLResponse(_form(_langs(pairs), validation_error(e).message), status_code=400)
    except ApiError as e:
        return HTMLResponse(_form(_langs(pairs), e.message), status_code=e.status)
    links = "".join(
        f"<p>Side {s.upper()} ({html.escape(lang)}): "
        f"<a href='{html.escape(url)}' target='_blank'>{html.escape(url)}</a></p>"
        for s, lang, url in (("a", req.a_lang, out.a_url), ("b", req.b_lang, out.b_url))
    )
    return HTMLResponse(
        _shell(
            f"<p>Call <code>{html.escape(out.call_id)}</code> created. Open one link per tab "
            f"or device.</p>{links}<p><a href='/loopback/new'>New call</a> · "
            f"<a href='/console/calls/{quote(out.call_id)}'>Console</a></p>"
        )
    )


@router.websocket("/ws/loopback")
async def loopback_socket(ws: WebSocket, sessions: Sessions, book: Book) -> None:
    call_id = ws.query_params.get("call") or ""
    side = ws.query_params.get("side")
    token = ws.query_params.get("t")
    await ws.accept()
    if side not in ("a", "b"):
        return await close_ws(ws, CLOSE_BAD_REQUEST)
    if token is not None and not verify_join_token(ws.app.state.settings, token, call_id, side):
        return await close_ws(ws, CLOSE_UNAUTHORIZED)
    session = sessions.get(call_id) if call_id else None
    if session is None:
        return await close_ws(ws, CLOSE_UNKNOWN_CALL)
    if not book.leg_attached(call_id, side):
        return await close_ws(ws, CLOSE_CONFLICT)
    leg = WebRtcLeg(side, ws)
    await serve_leg(ws, session, side, leg, book, call_id)
