"""Public landing page (/) and the stage demo dashboard (/demo): the landing page and the
sample-data preview need no password; the live dashboard starts calls, so it sits behind
the console auth like /join."""

import base64
import re

from tests.api.test_loopback import Env, _close_clients  # noqa: F401
from tests.join.test_routes import figures

FORM = {"content-type": "application/x-www-form-urlencoded"}


def basic(pw: str) -> dict[str, str]:
    return {"authorization": "Basic " + base64.b64encode(f"x:{pw}".encode()).decode()}


def start_demo(env: Env, a: str = "mr-IN", b: str = "hi-IN", headers=None):
    h = {**FORM, **(headers or {})}
    body = f"a_lang={a}&b_lang={b}&view=demo"
    return env.client.post("/join", content=body, headers=h, follow_redirects=False)


# --- landing ----------------------------------------------------------------------------


def test_landing_is_public():
    env = Env(dubhash_env="dev", console_password="pw")
    r = env.client.get("/", headers={"accept": "text/html"})
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_landing_tells_problem_solution_and_links_the_demo():
    r = Env().client.get("/")
    page = r.text
    assert "<title>" in page and "dubhash" in page
    for section in ('id="problem"', 'id="how"', 'id="features"', 'id="usecases"'):
        assert section in page, section
    assert 'href="/demo"' in page and 'href="/demo/preview"' in page
    # two animated phone mockups, one per caller
    assert len(re.findall(r'class="iphone[ "]', page)) == 2
    assert "/static/console/landing.css" in page and "/static/console/landing.js" in page


def test_landing_names_the_supported_languages():
    page = Env().client.get("/").text
    for native in ("मराठी", "हिन्दी", "ગુજરાતી", "English", "ಕನ್ನಡ", "தமிழ்"):
        assert native in page, native


# --- demo dashboard ---------------------------------------------------------------------


def test_demo_needs_console_auth():
    env = Env(dubhash_env="dev", console_password="pw")
    assert env.client.get("/demo").status_code == 401
    r = env.client.get("/demo", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/demo"
    assert env.client.get("/demo", headers=basic("pw")).status_code == 200


def test_demo_picker_offers_pairs_and_posts_to_join_with_view_demo():
    page = Env().client.get("/demo").text
    assert 'action="/join"' in page and 'name="view" value="demo"' in page
    offered = set(re.findall(r'data-pair="([^"]+)"', page))
    assert {"mr-IN|hi-IN", "mr-IN|en-IN", "gu-IN|en-IN"} <= offered
    assert 'href="/demo/preview"' in page


def test_start_with_view_demo_redirects_to_the_dashboard():
    env = Env()
    r = start_demo(env)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert re.fullmatch(r"/demo/c_\w+", loc), loc


def test_plain_join_still_redirects_to_join():
    env = Env()
    h = FORM
    r = env.client.post(
        "/join", content="a_lang=mr-IN&b_lang=hi-IN", headers=h, follow_redirects=False
    )
    assert r.headers["location"].startswith("/join/c_")


def test_dashboard_shows_qr_codes_and_wires_the_event_stream():
    env = Env()
    call_id = start_demo(env).headers["location"].rsplit("/", 1)[1]
    r = env.client.get(f"/demo/{call_id}")
    assert r.status_code == 200
    page = r.text
    figs = figures(page)
    assert set(figs) == {"a", "b"}
    for side, (url, svg) in figs.items():
        assert url.startswith(f"https://dev.dubhash.in/join/{call_id}/{side}?t=")
        assert svg.startswith("<svg")
    assert f'data-events="/console/events?call={call_id}"' in page
    assert 'id="demo"' in page and "/static/console/demo.js" in page
    for hook in ('id="timeline"', 'id="pipeline"', 'id="floor-lane"', 'id="ticker"'):
        assert hook in page, hook
    assert "मराठी" in page and "हिन्दी" in page


def test_dashboard_for_unknown_call_is_404():
    assert Env().client.get("/demo/c_nope").status_code == 404


def test_dashboard_needs_console_auth():
    env = Env(dubhash_env="dev", console_password="pw")
    call_id = start_demo(env, headers=basic("pw")).headers["location"].rsplit("/", 1)[1]
    assert env.client.get(f"/demo/{call_id}").status_code == 401
    assert env.client.get(f"/demo/{call_id}", headers=basic("pw")).status_code == 200


def test_preview_is_public_and_says_it_is_sample_data():
    env = Env(dubhash_env="dev", console_password="pw")
    r = env.client.get("/demo/preview")
    assert r.status_code == 200
    assert 'data-preview="1"' in r.text
    assert "Sample data" in r.text
    assert "data-events" not in r.text  # no live stream, no call created
