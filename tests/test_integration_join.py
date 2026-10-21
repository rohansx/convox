"""The stage demo end to end: the host page starts a call and shows two QR URLs; two
websocket clients play the phones, connecting with the token from those URLs (no console
auth). Real app, real CallSession and WebRtcLeg, fake vendors. Side B's phone also
follows the call's events over its token-authenticated SSE stream."""

from __future__ import annotations

import asyncio
import html
import json
import re
from urllib.parse import parse_qs, urlparse

import httpx
import websockets

from tests.audio_fixtures import silence, speech
from tests.test_integration_loopback import _collect, _speak, server  # noqa: F401

FORM = {"content-type": "application/x-www-form-urlencoded"}


async def _phone_events(url: str, sink: list[dict], stop: asyncio.Event) -> None:
    async with httpx.AsyncClient(timeout=None) as http:
        async with http.stream("GET", url) as r:
            assert r.status_code == 200
            kind = None
            async for line in r.aiter_lines():
                if stop.is_set():
                    return
                if line.startswith("event: "):
                    kind = line[7:]
                elif line.startswith("data: ") and kind:
                    sink.append({"kind": kind, **json.loads(line[6:])})


async def test_two_phones_join_by_qr_url_and_interpret(server):  # noqa: F811
    async with httpx.AsyncClient(base_url=f"http://{server}") as http:
        r = await http.post("/join", content="a_lang=mr-IN&b_lang=hi-IN", headers=FORM)
        assert r.status_code == 303, r.text
        call_id = r.headers["location"].rsplit("/", 1)[1]
        page = (await http.get(r.headers["location"])).text
        urls = {
            m.group(1): html.unescape(m.group(2))
            for m in re.finditer(r'<figure class="qr" data-side="(\w)" data-url="([^"]+)"', page)
        }
        assert set(urls) == {"a", "b"}
        tokens = {s: parse_qs(urlparse(u).query)["t"][0] for s, u in urls.items()}
        phone_b = await http.get(urlparse(urls["b"]).path, params={"t": tokens["b"]})
        assert phone_b.status_code == 200

    def sock(side: str) -> str:
        return f"ws://{server}/ws/loopback?call={call_id}&side={side}&t={tokens[side]}"

    events: list[dict] = []
    heard = {"a": bytearray(), "b": bytearray()}
    stop = asyncio.Event()
    sse = asyncio.create_task(
        _phone_events(f"http://{server}/join/{call_id}/b/events?t={tokens['b']}", events, stop)
    )
    await asyncio.sleep(0.2)
    async with websockets.connect(sock("a")) as wa, websockets.connect(sock("b")) as wb:
        readers = [
            asyncio.create_task(_collect(wa, stop, heard["a"])),
            asyncio.create_task(_collect(wb, stop, heard["b"])),
        ]
        await asyncio.sleep(1.0)  # opening line
        opening_b = len(heard["b"])
        await asyncio.gather(_speak(wa, speech(1200) + silence(1500)), _speak(wb, silence(2700)))
        await asyncio.sleep(1.0)
        assert len(heard["b"]) > opening_b, "B heard nothing after A spoke"
        stop.set()
        await asyncio.gather(*readers)
    sse.cancel()
    await asyncio.gather(sse, return_exceptions=True)

    kinds = [e["kind"] for e in events]
    assert "leg" in kinds and "turn" in kinds, kinds
    turn = next(e["turn"] for e in events if e["kind"] == "turn")
    assert turn["side"] == "a" and turn["call_id"] == call_id
