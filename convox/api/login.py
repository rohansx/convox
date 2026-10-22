"""Console login form. Sets the signed cookie that require_console_auth accepts."""

from __future__ import annotations

import html
import secrets
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from convox.api.deps import COOKIE, COOKIE_TTL_S, console_cookie

router = APIRouter()


def _safe_next(target: str | None) -> str:
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/console"
    return target


def _page(next_path: str, error: str | None = None) -> str:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>convox · sign in</title>
<style>
:root {{ --bg:#f6f5f2; --card:#fff; --fg:#1c1c1c; --muted:#6b6b6b;
  --accent:#7a2e12; --err:#b3261e; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#141414; --card:#1f1f1f; --fg:#eee; --muted:#9a9a9a; }} }}
body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg);
  color:var(--fg); font:16px/1.4 system-ui, -apple-system, Segoe UI, sans-serif; }}
form {{ background:var(--card); padding:32px; border-radius:12px; width:min(340px, 88vw);
  box-shadow:0 2px 16px rgba(0,0,0,.08); }}
h1 {{ margin:0 0 4px; font-size:28px; }} p {{ margin:0 0 20px; color:var(--muted); }}
input {{ width:100%; box-sizing:border-box; padding:12px; font-size:16px; border-radius:8px;
  border:1px solid #ccc; margin-bottom:16px; }}
button {{ width:100%; padding:12px; font-size:16px; border:0; border-radius:8px;
  background:var(--accent); color:#fff; cursor:pointer; }}
.err {{ color:var(--err); margin:-4px 0 12px; }}
</style></head><body>
<form method="post" action="/login">
<h1>convox</h1><p>Console password</p>{err}
<input type="password" name="password" autocomplete="current-password" autofocus required>
<input type="hidden" name="next" value="{html.escape(next_path)}">
<button type="submit">Sign in</button>
</form></body></html>"""


@router.get("/login", response_class=HTMLResponse)
async def login_form(next: str | None = None) -> HTMLResponse:
    return HTMLResponse(_page(_safe_next(next)))


@router.post("/login")
async def login_submit(request: Request):
    form = dict(parse_qsl((await request.body()).decode()))
    target = _safe_next(form.get("next"))
    expected = request.app.state.settings.console_password
    if not secrets.compare_digest(form.get("password", "").encode(), expected.encode()):
        return HTMLResponse(_page(target, "Wrong password."), status_code=401)
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        COOKIE,
        console_cookie(expected),
        max_age=COOKIE_TTL_S,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
    )
    return resp


@router.get("/logout")
async def logout() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp
