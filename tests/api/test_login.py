"""Console login page: browsers that never show the Basic-auth popup get a form instead."""

import base64

import httpx

from convox.api.app import create_app
from convox.config.settings import Settings

PW = "test-console-pw"


def app(**kw):
    s = Settings(_env_file=None, **{"convox_env": "dev", "console_password": PW, **kw})
    return create_app(s)


def client(a) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="https://t")


HTML = {"accept": "text/html,application/xhtml+xml"}


async def test_browser_without_auth_is_redirected_to_login():
    async with client(app()) as c:
        r = await c.get("/join", headers=HTML)
        assert r.status_code == 303
        assert r.headers["location"] == "/login?next=/join"


async def test_api_clients_still_get_basic_challenge():
    async with client(app()) as c:
        r = await c.get("/console")
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")


async def test_login_form_renders():
    async with client(app()) as c:
        r = await c.get("/login?next=/join")
        assert r.status_code == 200 and 'name="password"' in r.text and "/join" in r.text


async def test_good_password_sets_cookie_and_cookie_grants_access():
    async with client(app()) as c:
        r = await c.post("/login", data={"password": PW, "next": "/join"})
        assert r.status_code == 303 and r.headers["location"] == "/join"
        cookie = r.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie.replace("Lax", "lax")
        r = await c.get("/join", headers=HTML)
        assert r.status_code == 200


async def test_wrong_password_shows_form_again_without_cookie():
    async with client(app()) as c:
        r = await c.post("/login", data={"password": "nope", "next": "/join"})
        assert r.status_code == 401 and "set-cookie" not in r.headers
        assert 'name="password"' in r.text


async def test_next_must_be_a_local_path():
    async with client(app()) as c:
        for bad in ("https://evil.example/x", "//evil.example", "javascript:alert(1)"):
            r = await c.post("/login", data={"password": PW, "next": bad})
            assert r.headers["location"] == "/console", bad


async def test_cookie_from_old_password_is_rejected():
    a = app()
    async with client(a) as c:
        await c.post("/login", data={"password": PW, "next": "/join"})
        a.state.settings.console_password = "rotated-pw"
        r = await c.get("/join", headers=HTML)
        assert r.status_code == 303


async def test_basic_auth_still_works():
    token = base64.b64encode(f"x:{PW}".encode()).decode()
    async with client(app()) as c:
        r = await c.get("/console", headers={"authorization": f"Basic {token}"})
        assert r.status_code == 200


async def test_logout_clears_cookie():
    async with client(app()) as c:
        await c.post("/login", data={"password": PW, "next": "/join"})
        r = await c.get("/logout")
        assert r.status_code == 303
        c.cookies.clear()
        r = await c.get("/join", headers=HTML)
        assert r.status_code == 303
