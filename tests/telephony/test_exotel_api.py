import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest

from dubhash.telephony.base import DialError
from dubhash.telephony.exotel_api import ExotelDialer, parse_custom_field


def make(handler) -> ExotelDialer:
    return ExotelDialer(
        account_sid="acme1",
        api_key="KEY",
        api_token="TOKEN",
        host="https://api.in.exotel.com/",
        flow_url="http://my.exotel.com/acme1/exoml/start_voice/42",
        status_callback=lambda call_id, side: (
            f"https://api.x/webhooks/exotel?call={call_id}&side={side}"
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ring_timeout_s=25,
    )


async def test_dial_posts_calls_connect_with_flow_and_custom_field():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"Call": {"Sid": "exo-sid-1", "Status": "in-progress"}})

    d = make(handler)
    sid = await d.dial("c_abc", "a", "+919812345678", "+918047000000", "wss://e/ws/leg?call=c_abc")
    assert sid == "exo-sid-1"
    req = seen[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api.in.exotel.com/v1/Accounts/acme1/Calls/connect.json"
    auth = base64.b64decode(req.headers["authorization"].split()[1]).decode()
    assert auth == "KEY:TOKEN"
    form = parse_qs(req.content.decode())
    assert form["From"] == ["+919812345678"]
    assert form["CallerId"] == ["+918047000000"]
    assert form["Url"] == ["http://my.exotel.com/acme1/exoml/start_voice/42"]
    assert form["CustomField"] == ["c_abc:a"]
    assert form["StatusCallback"] == ["https://api.x/webhooks/exotel?call=c_abc&side=a"]
    assert form["StatusCallbackEvents[0]"] == ["terminal"]
    assert form["StatusCallbackEvents[1]"] == ["answered"]
    assert form["TimeOut"] == ["25"]


async def test_dial_error_carries_provider_message():
    def handler(req: httpx.Request) -> httpx.Response:
        body = {"RestException": {"Status": 400, "Message": "Invalid From number"}}
        return httpx.Response(400, content=json.dumps(body))

    with pytest.raises(DialError, match="Invalid From number"):
        await make(handler).dial("c_abc", "a", "+91x", "+918047000000", "wss://e")


async def test_dial_network_error_is_dial_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(DialError):
        await make(handler).dial("c_abc", "a", "+919812345678", "+918047000000", "wss://e")


def test_parse_custom_field():
    assert parse_custom_field("c_abc:b") == ("c_abc", "b")
    assert parse_custom_field("c_abc:z") is None
    assert parse_custom_field("") is None
    assert parse_custom_field(None) is None
