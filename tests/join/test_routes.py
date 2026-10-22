"""Stage demo pages: the host page with two QR codes and the phone page they open."""

import base64
import html
import re
from urllib.parse import parse_qs, urlparse

import pytest
import segno

from convox.config.pairs import find_pair, load_pairs
from convox.join import routes
from convox.join.tokens import make_join_token, verify_join_token
from tests.api.test_loopback import Env, _close_clients  # noqa: F401

FORM = {"content-type": "application/x-www-form-urlencoded"}


def basic(pw: str) -> dict[str, str]:
    return {"authorization": "Basic " + base64.b64encode(f"x:{pw}".encode()).decode()}


def start(env: Env, a: str = "mr-IN", b: str = "hi-IN", headers=None, **kw):
    h = {**FORM, **(headers or {})}
    return env.client.post("/join", content=f"a_lang={a}&b_lang={b}", headers=h, **kw)


def figures(page: str) -> dict[str, tuple[str, str]]:
    """side -> (url, inline svg) from the host page's QR figures."""
    out = {}
    for m in re.finditer(
        r'<figure class="qr" data-side="(\w)" data-url="([^"]+)">(.*?)</figure>', page, re.S
    ):
        svg = re.search(r"<svg.*?</svg>", m.group(3), re.S)
        out[m.group(1)] = (html.unescape(m.group(2)), svg.group(0) if svg else "")
    return out


def resolvable(env: Env) -> set[tuple[str, str]]:
    pairs = load_pairs(env.app.state.settings.pairs_file)
    langs = {p.side(s).lang for p in pairs.values() for s in "ab"}
    return {(a, b) for a in langs for b in langs if a != b and find_pair(pairs, a, b)}


# --- host page --------------------------------------------------------------------------


def test_host_page_lists_only_resolvable_pairs():
    env = Env()
    r = env.client.get("/join")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    offered = {tuple(p.split("|")) for p in re.findall(r'data-pair="([^"]+)"', r.text)}
    assert offered == resolvable(env)
    assert {("mr-IN", "hi-IN"), ("mr-IN", "en-IN"), ("hi-IN", "kn-IN"), ("hi-IN", "ta-IN")} <= (
        offered
    )
    for native in ("मराठी", "English", "ಕನ್ನಡ", "தமிழ்"):
        assert native in r.text
    assert "Start call" in r.text


def test_offered_pairs_skip_fakes_and_duplicates():
    env = Env()
    pairs = load_pairs(env.app.state.settings.pairs_file)
    offered = routes.offered_pairs(pairs)
    assert len(offered) == len(set(offered))
    assert ("hi-IN", "kn-IN") in offered  # hi-kn and hi-kn-direct are one choice
    assert offered[0] == ("mr-IN", "hi-IN")  # the stage pair comes first


def test_host_pages_need_console_auth_but_the_phone_page_does_not():
    env = Env(convox_env="dev", console_password="pw")
    assert env.client.get("/join").status_code == 401
    assert start(env).status_code == 401
    assert env.client.get("/join", headers=basic("pw")).status_code == 200
    r = start(env, headers=basic("pw"), follow_redirects=False)
    assert r.status_code == 303
    call_id = r.headers["location"].rsplit("/", 1)[1]
    assert env.client.get(f"/join/{call_id}").status_code == 401
    tok = make_join_token(env.app.state.settings, call_id, "a")
    assert env.client.get(f"/join/{call_id}/a?t={tok}").status_code == 200


def test_start_call_shows_two_qr_codes_for_the_public_url():
    env = Env()
    r = start(env, follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    call_id = loc.rsplit("/", 1)[1]
    assert loc == f"/join/{call_id}" and call_id.startswith("c_")
    session = env.sessions[0]
    assert session.call_id == call_id and session.pair.key == "mr-hi" and session.record

    page = env.client.get(loc)
    assert page.status_code == 200
    figs = figures(page.text)
    assert set(figs) == {"a", "b"}
    s = env.app.state.settings
    for side, (url, svg) in figs.items():
        u = urlparse(url)
        # settings.public_base_url, not the test client's host
        assert f"{u.scheme}://{u.netloc}" == "https://dev.convox.in"
        assert u.path == f"/join/{call_id}/{side}"
        assert verify_join_token(s, parse_qs(u.query)["t"][0], call_id, side)
        assert svg == routes.qr_svg(url)  # the QR encodes exactly that URL
        assert html.escape(url) in page.text  # printed under the code too
    assert "मराठी" in page.text and "हिन्दी" in page.text
    assert f"/console/events?call={call_id}" in page.text
    assert '<script src="http' not in page.text  # no CDN


def test_qr_svg_is_inline_and_self_contained():
    svg = routes.qr_svg("https://dev.convox.in/join/c_1/a?t=1.ab")
    assert svg.startswith("<svg") and "<?xml" not in svg and "viewBox" in svg
    q = segno.make("https://dev.convox.in/join/c_1/a?t=1.ab", error="m")
    assert q.error == "M"


def test_start_with_an_unsupported_pair_shows_the_form_again():
    env = Env()
    r = start(env, "hi-IN", "od-IN")
    assert r.status_code == 422 and "data-pair=" in r.text
    assert env.sessions == []


def test_host_view_of_unknown_call_is_404():
    assert Env().client.get("/join/c_nope").status_code == 404


# --- phone page -------------------------------------------------------------------------


def _call(env: Env) -> str:
    r = start(env, follow_redirects=False)
    return r.headers["location"].rsplit("/", 1)[1]


def test_phone_page_with_a_valid_token():
    env = Env()
    call_id = _call(env)
    tok = make_join_token(env.app.state.settings, call_id, "b")
    r = env.client.get(f"/join/{call_id}/b?t={tok}")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    text = html.unescape(r.text)
    assert "हिन्दी" in text and 'lang="hi"' in text
    assert "Tap to start" in text and "Leave call" in text
    assert f"/ws/loopback?call={call_id}&side=b&t={tok}" in text
    assert f"/join/{call_id}/b/events?t={tok}" in text
    assert r.headers.get("cache-control") == "no-store"


@pytest.mark.parametrize("which", ["missing", "garbage", "other_side", "other_call"])
def test_phone_page_with_a_bad_token_is_403(which):
    env = Env()
    call_id = _call(env)
    s = env.app.state.settings
    tok = {
        "missing": None,
        "garbage": "1.x",
        "other_side": make_join_token(s, call_id, "a"),
        "other_call": make_join_token(s, "c_other", "b"),
    }[which]
    q = "" if tok is None else f"?t={tok}"
    assert env.client.get(f"/join/{call_id}/b{q}").status_code == 403
    assert env.client.get(f"/join/{call_id}/b/events{q}").status_code == 403


def test_phone_page_for_an_ended_call_is_404():
    env = Env()
    tok = make_join_token(env.app.state.settings, "c_gone", "a")
    r = env.client.get(f"/join/c_gone/a?t={tok}")
    assert r.status_code == 404 and "ended" in r.text.lower()


def test_phone_page_bad_side_is_404():
    env = Env()
    call_id = _call(env)
    tok = make_join_token(env.app.state.settings, call_id, "c")
    assert env.client.get(f"/join/{call_id}/c?t={tok}").status_code == 404


def test_join_pages_off_with_the_webrtc_leg():
    env = Env(enable_webrtc_leg=False)
    assert env.client.get("/join").status_code == 404


def test_picker_and_phone_page_label_gujarati_in_its_own_script():
    env = Env()
    r = env.client.get("/join")
    offered = {tuple(p.split("|")) for p in re.findall(r'data-pair="([^"]+)"', r.text)}
    assert {("gu-IN", "hi-IN"), ("gu-IN", "en-IN")} <= offered
    assert "ગુજરાતી" in r.text
    assert set(routes.UI["gu"]) == set(routes.UI["en"])
    loc = start(env, "gu-IN", "hi-IN", follow_redirects=False).headers["location"]
    call_id = loc.rsplit("/", 1)[1]
    assert env.sessions[-1].pair.key == "gu-hi"
    tok = make_join_token(env.app.state.settings, call_id, "a")
    text = html.unescape(env.client.get(f"/join/{call_id}/a?t={tok}").text)
    assert "ગુજરાતી" in text and 'lang="gu"' in text
    assert routes.UI["gu"]["start"] in text
