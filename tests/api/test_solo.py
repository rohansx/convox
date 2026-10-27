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


def test_picker_is_public_and_offers_each_story_with_a_language_picker():
    env = Env(convox_env="dev", console_password="pw")
    r = env.client.get("/try")
    assert r.status_code == 200
    for story, agent_lang in (("auto", "kn-IN"), ("parcel", "mr-IN"), ("food", "ta-IN")):
        form = re.search(rf'<form[^>]*data-story="{story}".*?</form>', r.text, re.S).group(0)
        options = re.findall(r'<option value="([\w-]+)"', form)
        assert options[0] == "hi-IN" and agent_lang not in options and "en-IN" in options
        assert len(options) == 5, story
    assert "understand Hindi" in r.text  # the problem, on every card


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


def test_call_page_has_both_phones_the_start_button_and_token_urls():
    env = Env()
    r = start(env)
    page = env.client.get(r.headers["location"]).text
    call_id = re.search(r"/try/(c_\w+)", r.headers["location"]).group(1)
    t = parse_qs(urlparse(r.headers["location"]).query)["t"][0]
    assert 'id="start"' in page and "Start the call" in page and "/static/console/try.js" in page
    assert len(re.findall(r'class="iphone" data-side="[ab]"', page)) == 2
    assert f'data-ws="/ws/loopback?call={call_id}&amp;side=b&amp;t={t}"' in page
    assert f'data-monitor="/ws/try/{call_id}/monitor?story=auto&amp;t={t}"' in page
    assert f'data-events="/join/{call_id}/b/events?t={t}"' in page
    assert f'data-outcome="/try/{call_id}/outcome?story=auto&amp;t={t}"' in page
    assert 'class="ai-tag"' in page and "Hearing both phones" in page
    assert "Basavaraj" in page and "ಕನ್ನಡ" in page and "हिन्दी" in page


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
    assert start(env, story="parcel", lang="mr-IN").status_code == 400


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


def session_of(env: Env, r):
    return env.registry.get(re.search(r"/try/(c_\w+)", r.headers["location"]).group(1))


def test_both_sides_keep_their_own_script():
    env = Env(try_max_live=10)
    s = session_of(env, start(env))
    assert s.pair.key == "hi-kn-direct" and s.pair.b.stt == s.pair.a.stt == "sarvam"
    s = session_of(env, start(env, story="parcel", lang="hi-IN"))
    assert s.pair.key == "mr-hi-direct" and (s.pair.a.lang, s.pair.b.lang) == ("mr-IN", "hi-IN")
    # no pairs.yaml entry for Kannada and Tamil: one is built, native STT on both sides
    s = session_of(env, start(env, lang="ta-IN"))
    assert s.pair.key == "kn-ta-solo" and (s.pair.a.lang, s.pair.b.lang) == ("kn-IN", "ta-IN")
    assert s.pair.a.stt == s.pair.b.stt == "sarvam"
    assert s.pair.b.voice_m == "bulbul:ta-male-1"


def test_monitor_streams_the_agents_phone_after_its_first_line():
    env = Env()
    r = start(env)
    call_id = re.search(r"/try/(c_\w+)", r.headers["location"]).group(1)
    t = parse_qs(urlparse(r.headers["location"]).query)["t"][0]
    leg = session_of(env, r).legs["a"]
    with env.client.websocket_connect(f"/ws/try/{call_id}/monitor?story=auto&t={t}") as ws:
        wait_for(lambda: env.app.state.try_monitors[call_id]._subs)
        leg.tap("heard", b"\x01\x00")  # the opening prompt: not forwarded
        leg.tap("said", b"\x02\x00")
        leg.tap("heard", b"\x03\x00")
        leg.tap("clear", b"")
        assert ws.receive_bytes() == b"S\x02\x00"
        assert ws.receive_bytes() == b"H\x03\x00"
        assert ws.receive_json() == {"type": "clear"}


def test_monitor_needs_the_token():
    env = Env()
    call_id = re.search(r"/try/(c_\w+)", start(env).headers["location"]).group(1)
    with env.client.websocket_connect(f"/ws/try/{call_id}/monitor?story=auto&t=x") as ws:
        msg = ws.receive()
        assert msg["type"] == "websocket.close" and msg["code"] == 4401
