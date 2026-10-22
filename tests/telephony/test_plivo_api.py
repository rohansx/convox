import base64
import json
import xml.etree.ElementTree as ET

import httpx
import pytest

from convox.telephony.base import DialError
from convox.telephony.plivo_api import PlivoDialer, stream_xml


def make(handler) -> PlivoDialer:
    return PlivoDialer(
        auth_id="MAXXX",
        auth_token="tok",
        public_base_url="https://api.convox.in/",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ring_timeout_s=25,
    )


async def test_dial_posts_call_with_answer_url():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            201, json={"message": "call fired", "request_uuid": "req-1", "api_id": "x"}
        )

    sid = await make(handler).dial(
        "c_abc", "b", "+919876543210", "+918047000000", "wss://e/ws/leg?call=c_abc&side=b"
    )
    assert sid == "req-1"
    req = seen[0]
    assert str(req.url) == "https://api.plivo.com/v1/Account/MAXXX/Call/"
    assert base64.b64decode(req.headers["authorization"].split()[1]).decode() == "MAXXX:tok"
    body = json.loads(req.content)
    assert body["from"] == "918047000000" and body["to"] == "919876543210"
    assert body["answer_url"] == "https://api.convox.in/telephony/plivo/answer?call=c_abc&side=b"
    assert body["answer_method"] == "POST"
    assert body["hangup_url"] == "https://api.convox.in/webhooks/plivo?call=c_abc&side=b"
    assert body["ring_timeout"] == 25


async def test_dial_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid to number"})

    with pytest.raises(DialError, match="invalid to number"):
        await make(handler).dial("c_abc", "b", "+91x", "+918047000000", "wss://e")


def test_stream_xml_points_at_ws_url():
    url = "wss://e/ws/leg?call=c_abc&side=b&sample-rate=16000"
    xml = stream_xml(url, "audio/x-l16;rate=16000")
    root = ET.fromstring(xml)
    assert root.tag == "Response"
    stream = root.find("Stream")
    assert stream is not None
    assert stream.text.strip() == url
    assert stream.get("bidirectional") == "true"
    assert stream.get("keepCallAlive") == "true"
    assert stream.get("contentType") == "audio/x-l16;rate=16000"
    assert stream.get("audioTrack") == "inbound"
