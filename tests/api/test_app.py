import asyncio
import base64

import httpx
import pytest

from dubhash.api import app as app_module
from dubhash.api.app import create_app
from dubhash.api.deps import InMemoryCallRegistry
from dubhash.config.settings import Settings
from dubhash.events import EventBus
from dubhash.store.writer import TurnWriter
from dubhash.types import CallEvent, Turn


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **{"dubhash_env": "test", **kw})


@pytest.fixture
def registry():
    r = InMemoryCallRegistry()
    r.put(
        {
            "call_id": "c_91",
            "a_lang": "hi-IN",
            "b_lang": "kn-IN",
            "provider": "exotel",
            "status": "in_progress",
            "a_number": "+919876543210",
        }
    )
    r.apply_event(
        CallEvent("c_91", 1, "state", {"from": "IDLE", "to": "PLAYING_AB", "queue_depth": 1})
    )
    t = Turn(call_id="c_91", side="a", seq=1, src_text="bhaiya main gate ke bahar hoon",
             tgt_text="ನಾನು ಗೇಟ್ ಹೊರಗೆ ಇದ್ದೇನೆ", t_speech_end=7450, t_eou=8000, t_stt_final=8290,
             t_mt=8500, t_tts_ttfb=8800, t_play_start=8950)  # fmt: skip
    r.apply_event(CallEvent("c_91", 2, "turn", {"turn": t}))
    return r


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_healthz():
    async with client(create_app(settings())) as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


async def test_console_index_lists_calls(registry):
    async with client(create_app(settings(), registry=registry)) as c:
        r = await c.get("/console")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    body = r.text
    assert "c_91" in body and "hi-IN" in body and "kn-IN" in body
    assert "PLAYING_AB" in body
    assert "/console/calls/c_91" in body
    assert "htmx" in body
    assert "9876543210" not in body  # numbers are masked


async def test_console_calls_fragment(registry):
    async with client(create_app(settings(), registry=registry)) as c:
        r = await c.get("/console/_calls")
    assert r.status_code == 200 and "c_91" in r.text and "<html" not in r.text


async def test_console_call_view_renders_snapshot(registry):
    async with client(create_app(settings(), registry=registry)) as c:
        r = await c.get("/console/calls/c_91")
    assert r.status_code == 200
    body = r.text
    assert "bhaiya main gate ke bahar hoon" in body and "ನಾನು ಗೇಟ್ ಹೊರಗೆ ಇದ್ದೇನೆ" in body
    assert "PLAYING_AB" in body
    assert "1.50" in body  # first audio = 8950 - 7450
    assert "/console/events?call=c_91" in body


async def test_console_unknown_call_is_404(registry):
    async with client(create_app(settings(), registry=registry)) as c:
        r = await c.get("/console/calls/c_nope")
    assert r.status_code == 404


async def test_ward_stub():
    async with client(create_app(settings())) as c:
        r = await c.get("/ward")
    assert r.status_code == 200 and "Ward" in r.text


async def test_console_static_assets_served():
    async with client(create_app(settings())) as c:
        css = await c.get("/static/console/console.css")
        js = await c.get("/static/console/console.js")
    assert css.status_code == 200 and js.status_code == 200


async def test_legs_static_mounted_when_present(tmp_path, monkeypatch):
    (tmp_path / "leg.html").write_text("<p>leg</p>")
    monkeypatch.setattr(app_module, "LEGS_STATIC_DIR", tmp_path)
    async with client(create_app(settings())) as c:
        r = await c.get("/static/legs/leg.html")
    assert r.status_code == 200 and "leg" in r.text


async def test_legs_static_absent_is_fine(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "LEGS_STATIC_DIR", tmp_path / "missing")
    async with client(create_app(settings())) as c:
        r = await c.get("/static/legs/leg.html")
    assert r.status_code == 404


# --- auth -----------------------------------------------------------------------------


def basic(user: str, pw: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


async def test_console_requires_basic_auth_outside_test():
    app = create_app(settings(dubhash_env="dev", console_password="s3cret"))
    async with client(app) as c:
        assert (await c.get("/healthz")).status_code == 200
        r = await c.get("/console")
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
        assert (await c.get("/console", headers=basic("op", "wrong"))).status_code == 401
        assert (await c.get("/console", headers=basic("op", "s3cret"))).status_code == 200
        assert (await c.get("/console/events")).status_code == 401
        assert (await c.get("/ward")).status_code == 401


async def test_prod_refuses_default_console_password():
    app = create_app(settings(dubhash_env="prod", console_password="change-me"))
    async with client(app) as c:
        r = await c.get("/console", headers=basic("op", "change-me"))
    assert r.status_code == 503


# --- SSE ------------------------------------------------------------------------------


async def asgi_stream(app, path: str, query: str):
    """Drive a streaming GET directly over ASGI (httpx's ASGITransport buffers the whole
    body, which never ends for SSE). Returns (start message, chunk queue, disconnect)."""
    chunks: asyncio.Queue[bytes] = asyncio.Queue()
    start: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
    disconnected = asyncio.Event()
    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            start.set_result(msg)
        elif msg["type"] == "http.response.body" and msg.get("body"):
            await chunks.put(msg["body"])

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": query.encode(), "root_path": "",
        "headers": [(b"host", b"t"), (b"accept", b"text/event-stream")],
        "client": ("127.0.0.1", 1), "server": ("t", 80),
    }  # fmt: skip
    task = asyncio.create_task(app(scope, receive, send))
    return start, chunks, disconnected, task


async def test_sse_endpoint_streams_bus_events():
    bus = EventBus()
    app = create_app(settings(), bus=bus)
    start, chunks, disconnected, task = await asgi_stream(app, "/console/events", "call=c_91")
    msg = await asyncio.wait_for(start, 1)
    assert msg["status"] == 200
    headers = dict(msg["headers"])
    assert headers[b"content-type"].startswith(b"text/event-stream")
    assert headers.get(b"x-accel-buffering") == b"no"
    assert (await asyncio.wait_for(chunks.get(), 1)).startswith(b":")
    bus.publish(CallEvent("c_other", 1, "dtmf", {"side": "a", "digit": "1"}))
    bus.publish(CallEvent("c_91", 2, "dtmf", {"side": "b", "digit": "0"}))
    chunk = (await asyncio.wait_for(chunks.get(), 1)).decode()
    assert chunk.startswith("event: dtmf\n") and '"call_id": "c_91"' in chunk
    disconnected.set()
    await asyncio.wait_for(task, 1)
    assert bus.subscriber_count == 0


# --- lifespan -------------------------------------------------------------------------


async def test_lifespan_starts_and_closes_injected_writer(tmp_path):
    from dubhash.store.db import init_db, make_engine, make_sessionmaker

    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/l.db")
    await init_db(engine)
    writer = TurnWriter(make_sessionmaker(engine), flush_ms=10)
    app = create_app(settings(), writer=writer)
    async with app.router.lifespan_context(app):
        assert app.state.writer is writer
        assert writer._task is not None and not writer._task.done()
    assert writer._task.done()
    await engine.dispose()


async def test_lifespan_dev_creates_db_and_writer(tmp_path):
    app = create_app(
        settings(dubhash_env="dev", database_url=f"sqlite+aiosqlite:///{tmp_path}/d.db")
    )
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.writer, TurnWriter)
    assert (tmp_path / "d.db").exists()


async def test_lifespan_dev_unreachable_db_still_starts():
    url = "postgresql+asyncpg://u:p@127.0.0.1:1/x"
    app = create_app(settings(dubhash_env="dev", database_url=url))
    async with app.router.lifespan_context(app):
        assert app.state.writer is None
        async with client(app) as c:
            assert (await c.get("/healthz")).status_code == 200


async def test_lifespan_test_env_skips_db():
    app = create_app(settings(database_url="postgresql+asyncpg://u:p@127.0.0.1:1/x"))
    async with app.router.lifespan_context(app):
        assert app.state.writer is None


async def test_lifespan_feeds_bus_events_into_in_memory_registry():
    bus = EventBus()
    reg = InMemoryCallRegistry()
    reg.put({"call_id": "c_5"})
    app = create_app(settings(), bus=bus, registry=reg)
    async with app.router.lifespan_context(app):
        for _ in range(50):
            if bus.subscriber_count:
                break
            await asyncio.sleep(0.001)
        bus.publish(
            CallEvent("c_5", 1, "state", {"from": "IDLE", "to": "B_SPEAKING", "queue_depth": 0})
        )
        for _ in range(50):
            await asyncio.sleep(0.001)
            if reg.get_call("c_5")["state"] == "B_SPEAKING":
                break
        assert reg.get_call("c_5")["state"] == "B_SPEAKING"
