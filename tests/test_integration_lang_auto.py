"""lang "auto" through the real app: POST /loopback/calls with an auto side, two websocket
tabs, real CallSession and WebRtcLeg, fake vendors (DUBHASH_ENV=test, so FakeLID, which
is never sure). Two long utterances from the auto side get no confident guess, so its
leg hears the keypad menu; the page's digit picks Tamil; GET /v1/calls shows it and B's
reply is translated into Tamil."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import websockets

from tests.audio_fixtures import silence, speech
from tests.test_integration_loopback import _collect, _speak, serve


@pytest.fixture
def server():
    # Silero splits the synthetic speech and starts late: decide on shorter utterances
    with serve(lang_min_speech_ms=600) as host:
        yield host


async def test_auto_side_falls_back_to_the_keypad_menu_over_loopback(server):
    async with httpx.AsyncClient(base_url=f"http://{server}") as http:
        r = await http.post(
            "/loopback/calls", json={"a_lang": "auto", "b_lang": "kn-IN", "record": True}
        )
        assert r.status_code == 201, r.text
        call_id = r.json()["call_id"]
        assert "lang=auto" in r.json()["a_url"]

    url = f"ws://{server}/ws/loopback?call={call_id}"
    heard = {"a": bytearray(), "b": bytearray()}
    stop = asyncio.Event()
    async with (
        websockets.connect(f"{url}&side=a") as wa,
        websockets.connect(f"{url}&side=b") as wb,
    ):
        readers = [
            asyncio.create_task(_collect(wa, stop, heard["a"])),
            asyncio.create_task(_collect(wb, stop, heard["b"])),
        ]
        await asyncio.sleep(0.5)  # opening line
        two = speech(1700) + silence(1200) + speech(1700) + silence(1200)
        await asyncio.gather(_speak(wa, two), _speak(wb, silence(len(two) // 32)))
        await asyncio.sleep(0.8)  # the second utterance is judged, the menu starts
        before_digit = len(heard["a"])
        await wa.send(json.dumps({"type": "dtmf", "digit": "5"}))  # Tamil
        await asyncio.sleep(0.3)

        async with httpx.AsyncClient(base_url=f"http://{server}") as http:
            r = await http.get(
                f"/v1/calls/{call_id}", headers={"authorization": "Bearer change-me"}
            )
        await asyncio.gather(
            _speak(wb, speech(1200, f0=220) + silence(1500)), _speak(wa, silence(2700))
        )
        await asyncio.sleep(1.0)
        assert len(heard["a"]) > before_digit, "A heard nothing after B spoke"
        stop.set()
        await asyncio.gather(*readers)

    assert r.status_code == 200, r.text
    legs = r.json()["legs"]
    assert legs["a"]["lang"] == "auto"
    assert legs["a"]["detected"] == {"lang": "ta-IN", "state": "CONFIRMED", "auto": True}
    assert legs["b"]["detected"]["lang"] == "kn-IN"

    async with httpx.AsyncClient(base_url=f"http://{server}") as http:
        turns = (await http.get(f"/v1/calls/{call_id}/turns")).json()["turns"]
    b_turns = [t for t in turns if t["side"] == "b"]
    assert b_turns and b_turns[-1]["tgt_text"].startswith("[ta-IN]")
    a_turns = [t for t in turns if t["side"] == "a"]
    assert [t["tgt_text"][:7] for t in a_turns] == ["[kn-IN]", "[kn-IN]"]
