"""The live demo stage (/demo): stories, role-based QR join, the live stage, the outcome
card and the engineering dashboard moved to /demo/{id}/details."""

import html
import re

from convox.api.deps import get_registry
from tests.api.test_loopback import Env, _close_clients  # noqa: F401
from tests.api.test_site import FORM, basic, start_demo
from tests.join.test_routes import figures


def start_story(env: Env, story: str, a: str, b: str, headers=None):
    h = {**FORM, **(headers or {})}
    body = f"pair={a}|{b}&story={story}&view=demo"
    return env.client.post("/join", content=body, headers=h, follow_redirects=False)


def call_of(r) -> str:
    return re.search(r"/demo/(c_\w+)", r.headers["location"]).group(1)


# --- picker -----------------------------------------------------------------------------


def test_picker_offers_the_three_stories_as_one_click_starts():
    page = Env().client.get("/demo").text
    for story, pair in (
        ("rent", "kn-IN|hi-IN"),
        ("delivery", "mr-IN|hi-IN"),
        ("clinic", "gu-IN|en-IN"),
    ):
        form = re.search(rf'<form[^>]*data-story="{story}".*?</form>', page, re.S)
        assert form, story
        f = form.group(0)
        assert 'action="/join"' in f and f'value="{pair}"' in f
        assert f'name="story" value="{story}"' in f and 'name="view" value="demo"' in f
    assert page.index('data-story="rent"') < page.index('data-story="delivery"')
    assert "Landlord" in page and "Tenant" in page


def test_start_with_a_story_keeps_it_on_the_stage_url_and_sets_the_languages():
    env = Env()
    r = start_story(env, "rent", "kn-IN", "hi-IN")
    assert r.status_code == 303
    assert re.fullmatch(r"/demo/c_\w+\?story=rent", r.headers["location"])
    page = env.client.get(r.headers["location"]).text
    assert 'data-a-native="ಕನ್ನಡ"' in page and 'data-b-native="हिन्दी"' in page


def test_unknown_story_is_dropped():
    r = start_story(Env(), "nope", "kn-IN", "hi-IN")
    assert re.fullmatch(r"/demo/c_\w+", r.headers["location"])


# --- the stage --------------------------------------------------------------------------


def test_stage_shows_roles_and_qr_codes_that_carry_the_story():
    env = Env()
    call_id = call_of(start_story(env, "rent", "kn-IN", "hi-IN"))
    page = env.client.get(f"/demo/{call_id}?story=rent").text
    assert 'id="stage"' in page and "/static/console/stage.js" in page
    assert "Landlord" in page and "Tenant" in page and "The water has been off" in page
    figs = figures(page)
    assert set(figs) == {"a", "b"}
    for side, (url, svg) in figs.items():
        assert url.startswith(f"https://dev.convox.in/join/{call_id}/{side}?t=")
        assert url.endswith("&story=rent") and svg.startswith("<svg")
    assert f'data-events="/console/events?call={call_id}"' in page
    assert f'data-outcome="/demo/{call_id}/outcome?story=rent"' in page
    assert f'href="/demo/{call_id}/details"' in page
    assert "ಕನ್ನಡ" in page and "हिन्दी" in page


def test_stage_without_a_story_uses_generic_roles():
    env = Env()
    call_id = call_of(start_demo(env))
    page = env.client.get(f"/demo/{call_id}").text
    assert 'id="stage"' in page and "Person A" in page
    for url, _ in figures(page).values():
        assert "story=" not in url


def test_stage_needs_console_auth_and_404s_unknown_calls():
    env = Env(convox_env="dev", console_password="pw")
    call_id = call_of(start_story(env, "rent", "kn-IN", "hi-IN", headers=basic("pw")))
    assert env.client.get(f"/demo/{call_id}").status_code == 401
    assert env.client.get(f"/demo/{call_id}", headers=basic("pw")).status_code == 200
    assert env.client.get("/demo/c_nope", headers=basic("pw")).status_code == 404


def test_details_is_the_engineering_dashboard():
    env = Env()
    call_id = call_of(start_demo(env))
    page = env.client.get(f"/demo/{call_id}/details").text
    assert 'id="demo"' in page and "/static/console/demo.js" in page
    for hook in ('id="timeline"', 'id="pipeline"', 'id="floor-lane"', 'id="ticker"'):
        assert hook in page, hook
    assert f'data-events="/console/events?call={call_id}"' in page
    assert env.client.get("/demo/c_nope/details").status_code == 404


# --- preview ----------------------------------------------------------------------------


def test_preview_stage_is_public_scripted_and_links_its_details():
    env = Env(convox_env="dev", console_password="pw")
    page = env.client.get("/demo/preview").text
    assert 'id="stage"' in page and 'data-preview="1"' in page and "Sample data" in page
    assert "data-events" not in page and "Landlord" in page
    assert 'href="/demo/preview/details"' in page
    details = env.client.get("/demo/preview/details")
    assert details.status_code == 200 and 'id="demo"' in details.text


# --- the phone's role card --------------------------------------------------------------


def phone_url(env: Env, call_id: str, side: str, story: str) -> str:
    page = env.client.get(f"/demo/{call_id}?story={story}").text
    url = figures(page)[side][0]
    return url.replace("https://dev.convox.in", "")


def test_phone_shows_its_role_and_lines_to_try():
    env = Env()
    call_id = call_of(start_story(env, "rent", "kn-IN", "hi-IN"))
    page = env.client.get(phone_url(env, call_id, "a", "rent")).text
    assert "Landlord" in page and 'class="hints"' in page
    assert html.escape("ಇವತ್ತು ಸಂಜೆ 6 ಗಂಟೆಗೆ ಪ್ಲಂಬರ್ ಕಳುಹಿಸುತ್ತೇನೆ.") in page
    page_b = env.client.get(phone_url(env, call_id, "b", "rent")).text
    assert "Tenant" in page_b and "दो दिन से पानी नहीं आ रहा है।" in page_b


def test_phone_without_a_story_has_no_hints():
    env = Env()
    call_id = call_of(start_demo(env))
    url = figures(env.client.get(f"/demo/{call_id}").text)["a"][0]
    page = env.client.get(url.replace("https://dev.convox.in", "") + "&story=nope").text
    assert 'class="hints"' not in page


# --- the outcome card -------------------------------------------------------------------


class Stub:
    def __init__(self, call):
        self.call = call

    def get_call(self, call_id):
        return self.call if call_id == self.call["call_id"] else None


TURNS = [
    {"side": "b", "seq": 1, "src_text": "दो दिन से पानी नहीं आ रहा है।", "tgt_text": "x"},
    {"side": "a", "seq": 2, "src_text": "ಸಂಜೆ 6 ಗಂಟೆಗೆ ಪ್ಲಂಬರ್ ಕಳುಹಿಸ್ತೀನಿ.", "tgt_text": "y"},
]


def outcome_env(**kw):
    env = Env(**kw)
    stub = Stub({"call_id": "c_x", "a_lang": "kn-IN", "b_lang": "hi-IN", "turns": TURNS})
    env.app.dependency_overrides[get_registry] = lambda: stub
    seen = []

    async def fake(turns, story, langs):
        seen.append((turns, story, langs))
        return {
            "a": {"headline": "ಪ್ಲಂಬರ್ 6ಕ್ಕೆ", "points": []},
            "b": {"headline": "प्लंबर 6 बजे", "points": []},
        }

    env.app.state.summarise = fake
    return env, seen


def test_outcome_summarises_once_per_call_and_caches():
    env, seen = outcome_env()
    r = env.client.post("/demo/c_x/outcome?story=rent")
    assert r.status_code == 200
    assert r.json()["outcome"]["b"]["headline"] == "प्लंबर 6 बजे"
    turns, story, langs = seen[0]
    assert story["a"]["role"] == "Landlord" and langs == {"a": "kn-IN", "b": "hi-IN"}
    assert [t["seq"] for t in turns] == [1, 2]
    env.client.post("/demo/c_x/outcome?story=rent")
    assert len(seen) == 1


def test_outcome_without_a_key_is_null_not_an_error():
    env = Env(anthropic_api_key="")
    stub = Stub({"call_id": "c_x", "a_lang": "kn-IN", "b_lang": "hi-IN", "turns": TURNS})
    env.app.dependency_overrides[get_registry] = lambda: stub
    r = env.client.post("/demo/c_x/outcome")
    assert r.status_code == 200 and r.json() == {"outcome": None}


def test_outcome_needs_auth_and_a_known_call():
    env, _ = outcome_env(convox_env="dev", console_password="pw")
    assert env.client.post("/demo/c_x/outcome").status_code == 401
    assert env.client.post("/demo/c_nope/outcome", headers=basic("pw")).status_code == 404
