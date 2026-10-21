import pytest

from dubhash.config.settings import Settings
from dubhash.telephony import ProviderUnavailable, build_dialer
from dubhash.telephony.base import Dialer, FakeDialer, leg_ws_url, status_callback_url
from dubhash.telephony.exotel_api import ExotelDialer
from dubhash.telephony.plivo_api import PlivoDialer


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **{"dubhash_env": "test", **kw})


def test_leg_ws_url_has_call_side_and_rate():
    s = settings(edge_wss_base="wss://edge.dubhash.in/")
    assert (
        leg_ws_url(s, "c_abc", "b")
        == "wss://edge.dubhash.in/ws/leg?call=c_abc&side=b&sample-rate=16000"
    )


def test_status_callback_url():
    s = settings(public_base_url="https://api.dubhash.in")
    assert (
        status_callback_url(s, "exotel", "c_1", "a")
        == "https://api.dubhash.in/webhooks/exotel?call=c_1&side=a"
    )


async def test_fake_dialer_records_calls_and_returns_sids():
    d = FakeDialer()
    assert isinstance(d, Dialer)
    sid_a = await d.dial("c_1", "a", "+919812345678", "+918047000000", "wss://x/ws/leg?a")
    sid_b = await d.dial("c_1", "b", "+919876543210", "+918047000000", "wss://x/ws/leg?b")
    assert sid_a != sid_b
    assert [c.side for c in d.calls] == ["a", "b"]
    assert d.calls[0].number == "+919812345678" and d.calls[0].ws_url == "wss://x/ws/leg?a"


async def test_fake_dialer_can_fail():
    d = FakeDialer(fail_with="number blocked")
    with pytest.raises(Exception, match="number blocked"):
        await d.dial("c_1", "a", "+919812345678", "+918047000000", "wss://x")


def test_build_dialer_needs_credentials():
    with pytest.raises(ProviderUnavailable):
        build_dialer("exotel", settings())
    with pytest.raises(ProviderUnavailable):
        build_dialer("plivo", settings())
    with pytest.raises(ProviderUnavailable):
        build_dialer("nope", settings())


def test_build_dialer_with_credentials():
    ex = build_dialer(
        "exotel",
        settings(
            exotel_account_sid="sid",
            exotel_api_key="k",
            exotel_api_token="t",
            exotel_api_host="api.in.exotel.com",
            exotel_flow_url="http://my.exotel.com/sid/exoml/start_voice/1",
        ),
    )
    assert isinstance(ex, ExotelDialer)
    pl = build_dialer("plivo", settings(plivo_auth_id="MA1", plivo_auth_token="tok"))
    assert isinstance(pl, PlivoDialer)
    assert isinstance(build_dialer("fake", settings()), FakeDialer)
