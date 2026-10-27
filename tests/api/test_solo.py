"""The solo demo (/try, api/solo.py): public, capped, the agent on the story's side and
the visitor's mic in the stage page with a join token."""

import re
from urllib.parse import parse_qs, urlparse

from convox.demo.agent import AgentLeg
from tests.api.test_loopback import Env, _close_clients, wait_for  # noqa: F401
from tests.api.test_site import FORM


def start(env: Env, lang: str = "hi-IN", story: str = "auto", ip: str = "203.0.113.7"):
    headers = {**FORM, "x-forwarded-for": f"198.51.100.1, {ip}"}
    body = f"story={story}&lang={lang}"
    return env.client.post("/try", content=body, headers=headers, follow_redirects=False)


def test_picker_is_public_and_offers_the_visitor_languages():
    env = Env(convox_env="dev", console_password="pw")
    r = env.client.get("/try")
    assert r.status_code == 200
    assert "ಕನ್ನಡ" in r.text and 'action="/try"' in r.text
    assert 'value="hi-IN"' in r.text and 'value="kn-IN"' not in r.text


def test_start_attaches_the_agent_and_hands_the_visitor_a_token():
    env = Env()
    r = start(env)
    assert r.status_code == 303
    loc = urlparse(r.headers["location"])
    call_id = re.fullmatch(r"/try/(c_\w+)", loc.path).group(1)
    q = parse_qs(loc.query)
    assert q["story"] == ["auto"] and q["t"][0]
    session = env.registry.get(call_id)
    assert isinstance(session.legs["a"], AgentLeg) and "b" not in session.legs
    assert session.kw["co_located"] is False and session.record is False
    assert (session.pair.a.lang, session.pair.b.lang) == ("kn-IN", "hi-IN")


def test_stage_has_the_mic_the_ai_tag_and_token_urls():
    env = Env()
    r = start(env)
    page = env.client.get(r.headers["location"]).text
    call_id = re.search(r"/try/(c_\w+)", r.headers["location"]).group(1)
    t = parse_qs(urlparse(r.headers["location"]).query)["t"][0]
    assert 'class="solo-mic" id="phone"' in page and "/static/console/join_phone.js" in page
    assert f'data-ws="/ws/loopback?call={call_id}&amp;side=b&amp;t={t}"' in page
    assert f'data-events="/join/{call_id}/b/events?t={t}"' in page
    assert f'data-outcome="/try/{call_id}/outcome?story=auto&amp;t={t}"' in page
    assert 'class="ai-tag"' in page and "Under the hood" not in page
    assert "Basavaraj" in page and "You" in page


def test_stage_needs_the_visitors_token():
    env = Env()
    loc = start(env).headers["location"]
    call_id = re.search(r"/try/(c_\w+)", loc).group(1)
    assert env.client.get(f"/try/{call_id}?story=auto&t=nope").status_code == 403
    assert env.client.post(f"/try/{call_id}/outcome?story=auto&t=nope").status_code == 403


def test_outcome_is_null_without_a_key():
    env = Env()
    loc = start(env).headers["location"]
    path, _, query = loc.partition("?")
    r = env.client.post(f"{path}/outcome?{query}")
    assert r.status_code == 200 and r.json() == {"outcome": None}


def test_unknown_language_or_story_is_refused():
    env = Env()
    assert start(env, lang="xx-IN").status_code == 400
    assert start(env, lang="kn-IN").status_code == 400  # the agent's own language
    assert start(env, story="rent").status_code == 400  # no agent in this story


def test_calls_per_visitor_are_capped():
    env = Env(try_per_ip_day=2, try_max_live=10)
    assert start(env).status_code == 303
    assert start(env).status_code == 303
    r = start(env)
    assert r.status_code == 429 and "Book a demo" in r.text
    assert start(env, ip="203.0.113.8").status_code == 303  # another visitor


def test_calls_at_once_are_capped_and_freed_when_a_call_ends():
    env = Env(try_max_live=1)
    loc = start(env).headers["location"]
    assert start(env, ip="203.0.113.9").status_code == 429
    session = env.registry.get(re.search(r"/try/(c_\w+)", loc).group(1))
    env.client.portal.call(session.hangup)
    wait_for(lambda: not env.app.state.try_gate.live)
    assert start(env, ip="203.0.113.9").status_code == 303


def test_unanswered_calls_are_dropped():
    env = Env(try_start_s=0)
    loc = start(env).headers["location"]
    session = env.registry.get(re.search(r"/try/(c_\w+)", loc).group(1))
    wait_for(lambda: session.hangups == ["no_answer"])


def test_off_switch():
    env = Env(try_enabled=False)
    assert env.client.get("/try").status_code == 404


def test_hindi_visitors_get_the_direct_pair_so_they_see_their_own_words():
    env = Env()
    loc = start(env).headers["location"]
    session = env.registry.get(re.search(r"/try/(c_\w+)", loc).group(1))
    assert session.pair.key == "hi-kn-direct"
    assert session.pair.side("b").stt == "sarvam"
