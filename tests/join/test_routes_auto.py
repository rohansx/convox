"""/join with lang "auto": an Auto-detect choice per side on the picker, and a neutral phone
page until the side's language is detected."""

import html
import json
import re

from convox.join.tokens import make_join_token
from tests.api.test_loopback import Env, _close_clients  # noqa: F401
from tests.join.test_routes import start


def test_picker_offers_auto_detect_per_side():
    r = Env().client.get("/join")
    for side in ("a", "b"):
        select = re.search(rf'<select name="{side}_lang".*?</select>', r.text, re.S)
        assert select, side
        assert '<option value="auto"' in select.group(0)
        assert "Auto-detect" in select.group(0)
        for lang in ("hi-IN", "mr-IN", "gu-IN", "kn-IN", "ta-IN", "en-IN"):
            assert f'value="{lang}"' in select.group(0)


def test_start_with_an_auto_side():
    env = Env()
    r = start(env, "auto", "hi-IN", follow_redirects=False)
    assert r.status_code == 303
    session = env.sessions[0]
    assert session.kw["auto"].hints == {"a": None}
    assert session.pair.key == "auto-hi"
    page = env.client.get(r.headers["location"])
    assert page.status_code == 200
    assert "Auto-detect" in page.text


def test_start_with_an_auto_side_and_a_hint():
    env = Env()
    r = start(env, "kn-IN", "auto&b_lang_hint=ta-IN", follow_redirects=False)
    assert r.status_code == 303
    assert env.sessions[0].kw["auto"].hints == {"b": "ta-IN"}


def test_phone_page_of_an_auto_side_is_neutral_until_detected():
    env = Env()
    r = start(env, "auto", "hi-IN", follow_redirects=False)
    call_id = r.headers["location"].rsplit("/", 1)[1]
    tok = make_join_token(env.app.state.settings, call_id, "a")
    page = env.client.get(f"/join/{call_id}/a?t={tok}")
    assert page.status_code == 200
    text = html.unescape(page.text)
    assert "अपनी भाषा में बोलिए" in text and "Speak in your language" in text
    assert 'data-lang="auto"' in text
    names = json.loads(re.search(r"data-names='([^']+)'", page.text).group(1))
    assert names["ta-IN"] == ["Tamil", "தமிழ்"]
    # the partner's page names A neutrally too
    tok_b = make_join_token(env.app.state.settings, call_id, "b")
    text_b = html.unescape(env.client.get(f"/join/{call_id}/b?t={tok_b}").text)
    assert "हिन्दी" in text_b and "अपनी भाषा" in text_b
