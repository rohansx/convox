"""Public landing page (/) and the stage demo dashboard (/demo): the landing page and the
sample-data preview need no password; the live dashboard starts calls, so it sits behind
the console auth like /join."""

import base64
import json
import re
from pathlib import Path

from tests.api.test_loopback import Env, _close_clients  # noqa: F401

FORM = {"content-type": "application/x-www-form-urlencoded"}


def basic(pw: str) -> dict[str, str]:
    return {"authorization": "Basic " + base64.b64encode(f"x:{pw}".encode()).decode()}


def start_demo(env: Env, a: str = "mr-IN", b: str = "hi-IN", headers=None):
    h = {**FORM, **(headers or {})}
    body = f"a_lang={a}&b_lang={b}&view=demo"
    return env.client.post("/join", content=body, headers=h, follow_redirects=False)


# --- landing ----------------------------------------------------------------------------


def test_landing_is_public():
    env = Env(convox_env="dev", console_password="pw")
    r = env.client.get("/", headers={"accept": "text/html"})
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_landing_tells_problem_solution_and_links_the_demo():
    r = Env().client.get("/")
    page = r.text
    assert "<title>" in page and "convox" in page
    for section in ('id="problem"', 'id="how"', 'id="features"', 'id="usecases"'):
        assert section in page, section
    assert 'href="/try"' in page and 'href="/demo/preview"' in page
    # two animated phone mockups, one per caller
    assert len(re.findall(r'class="iphone[ "]', page)) == 2
    assert "/static/console/landing.css" in page and "/static/console/landing.js" in page


def test_landing_names_the_supported_languages():
    page = Env().client.get("/").text
    for native in ("मराठी", "हिन्दी", "ગુજરાતી", "English", "ಕನ್ನಡ", "தமிழ்"):
        assert native in page, native


# --- demo dashboard ---------------------------------------------------------------------


def test_demo_needs_console_auth():
    env = Env(convox_env="dev", console_password="pw")
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


def test_dashboard_for_unknown_call_is_404():
    assert Env().client.get("/demo/c_nope").status_code == 404


def test_dashboard_needs_console_auth():
    env = Env(convox_env="dev", console_password="pw")
    call_id = start_demo(env, headers=basic("pw")).headers["location"].rsplit("/", 1)[1]
    assert env.client.get(f"/demo/{call_id}").status_code == 401
    assert env.client.get(f"/demo/{call_id}", headers=basic("pw")).status_code == 200


def test_preview_is_public_and_says_it_is_sample_data():
    env = Env(convox_env="dev", console_password="pw")
    r = env.client.get("/demo/preview")
    assert r.status_code == 200
    assert 'data-preview="1"' in r.text
    assert "Sample data" in r.text
    assert "data-events" not in r.text  # no live stream, no call created


# --- Convox brand and the landing call's real audio --------------------------------------

STATIC = Path(__file__).resolve().parents[2] / "convox" / "console"


def test_landing_is_branded_convox():
    page = Env().client.get("/").text
    assert "<title>Convox AI" in page and ">CONVOX<" in page


def test_no_page_or_asset_still_says_dubhash():
    for f in [*STATIC.glob("templates/*.html"), *STATIC.glob("static/*.*")]:
        assert "dubhash" not in f.read_text(encoding="utf-8").lower(), f.name


def test_landing_has_a_sound_toggle_off_by_default():
    page = Env().client.get("/").text
    assert 'data-sound-toggle aria-pressed="false"' in page
    assert "Turn sound on" in page


def test_every_landing_line_has_real_audio_and_timings():
    data = json.loads((STATIC / "static" / "landing" / "scenes.json").read_text())
    assert set(data["scenes"]) == {"auto", "delivery", "clinic", "rent"}
    for scene in data["scenes"].values():
        assert scene["lines"]
        for line in scene["lines"]:
            for key in ("src_audio", "tgt_audio"):
                f = STATIC / "static" / "landing" / line[key]
                assert f.is_file() and f.stat().st_size > 1000, line[key]
            assert line["tgt"] and line["src"] != line["tgt"]
            assert line["mt_ms"] > 0 and line["tts_ttfb_ms"] > 0


def test_landing_audio_is_served():
    env = Env()
    r = env.client.get("/static/console/landing/scenes.json")
    assert r.status_code == 200
    first = r.json()["scenes"]["delivery"]["lines"][0]["tgt_audio"]
    assert env.client.get(f"/static/console/landing/{first}").status_code == 200


def test_landing_uses_geist_with_light_headings():
    # the YC W25-W26 sites we modelled this on: Geist/grotesk, headings at regular weight
    page = Env().client.get("/").text
    assert "family=Geist" in page and "family=Inter" not in page
    css = (STATIC / "static" / "landing.css").read_text(encoding="utf-8")
    assert '--sans: "Geist"' in css
    h1 = re.search(r"\.hero h1 \{[^}]*\}", css).group(0)
    assert "font-weight: 400" in h1


def test_preview_embed_mode_fits_the_landing_frame():
    env = Env()
    assert "is-embed" in env.client.get("/demo/preview?embed=1").text
    assert "is-embed" not in env.client.get("/demo/preview").text
    assert 'data-src="/demo/preview?embed=1"' in env.client.get("/").text


def test_brand_is_convox_ai_with_favicon():
    env = Env()
    for url in ("/", "/demo/preview"):
        page = env.client.get(url).text
        assert "CONVOX<em>AI</em>" in page and "/static/console/favicon.svg" in page, url
        assert 'class="mark wave' in page, url
    assert env.client.get("/static/console/favicon.svg").status_code == 200


def test_landing_puts_kannada_first():
    page = Env().client.get("/").text
    langs = re.search(r'<p class="langs"[^>]*>(.*?)</p>', page).group(1)
    assert re.sub(r"<[^>]+>", "", langs).startswith("ಕನ್ನಡ")
    tabs = re.findall(r'data-scene="(\w+)"', page)
    assert tabs[0] == "auto" and "rent" in tabs  # the auto call leads
    scenes = json.loads((STATIC / "static" / "landing" / "scenes.json").read_text())["scenes"]
    assert scenes["auto"]["a"]["lang"] == "kn-IN" and "auto driver" in scenes["auto"]["caption"]
    rent = json.loads((STATIC / "static" / "landing" / "scenes.json").read_text())["scenes"]["rent"]
    assert rent["a"]["lang"] == "kn-IN" and "landlord" in rent["caption"]


def test_landing_leads_with_rider_and_delivery_calls_and_the_masked_call():
    page = Env().client.get("/").text
    # every language, not one: the headline names none
    assert "Speak your language." in page and "Be heard in theirs." in page
    assert "delivery partner" in page
    for who in ("Food and quick commerce", "Bike taxis and autos", "Parcel and e-commerce"):
        assert who in page, who
    assert "masked call" in page and "CPaaS" in page and "niti.gov.in" in page
    assert 'id="platform"' in page and "not another app" in page
    assert "theweek.in" in page  # the auto rows are sourced, not asserted


# --- analytics --------------------------------------------------------------------------


def test_no_analytics_script_unless_configured():
    env = Env()
    for path in ("/", "/demo/preview"):
        assert "data-website-id" not in env.client.get(path).text, path


def test_umami_script_on_public_pages_when_configured():
    env = Env(umami_website_id="site-123", umami_script_url="https://stats.example/script.js")
    for path in ("/", "/demo/preview"):
        page = env.client.get(path).text
        assert 'src="https://stats.example/script.js" data-website-id="site-123"' in page, path


def test_landing_books_a_demo_with_the_founder():
    page = Env().client.get("/").text
    assert page.count('href="https://cal.com/rohansx/30min"') >= 2
    assert "Book a demo" in page


def test_no_waitlist_unless_configured():
    page = Env().client.get("/").text
    assert 'id="waitlist"' not in page
    assert "early access" not in page


def test_landing_waitlist_when_configured():
    env = Env(lobby_project_key="pk_test", lobby_script_url="https://wait.example/v1/wl.js")
    page = env.client.get("/").text
    assert '<div class="waitlist" id="waitlist"></div>' in page
    assert 'src="https://wait.example/v1/wl.js" data-project="pk_test"' in page
    assert 'data-target="#waitlist"' in page
