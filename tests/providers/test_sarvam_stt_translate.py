from urllib.parse import parse_qs, urlparse

from dubhash.interp.contracts import STT, STTStream
from dubhash.providers.sarvam_stt_translate import SarvamSTTTranslate
from tests.providers.wsmock import Conn, ws_server

F1 = b"\x01\x00" * 320


async def _utterance(conn: Conn, partial: str, final: str) -> None:
    await conn.recv_until(lambda m: m.get("event") == "speech_start")
    await conn.send({"event": "transcript.partial", "utterance_idx": 0, "text": partial})
    await conn.recv_until(lambda m: m.get("event") == "speech_end")
    await conn.send({"event": "transcript.final", "utterance_idx": 0, "text": final})


async def test_translate_mode_returns_english_final_and_source_partials():
    async def handler(conn: Conn) -> None:
        await _utterance(conn, "भैया मैं गेट", "Brother, I am outside the gate.")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = SarvamSTTTranslate(api_key="sk-test", base_url=srv.url, final_timeout_s=1.0)
        assert isinstance(stt, STT) and stt.name == "sarvam-translate"
        stream = await stt.open("hi-IN")
        assert isinstance(stream, STTStream)
        await stream.push(F1)
        final = await stream.end()
        partials = [p async for p in stream.partials()]
        await stream.aclose()
        await stt.aclose()

    assert final == "Brother, I am outside the gate."
    # Sarvam: "Partials are always plain transcription" (source language).
    assert partials == ["भैया मैं गेट"]
    conn = srv.conns[0]
    assert conn.headers["api-subscription-key"] == "sk-test"
    q = parse_qs(urlparse(conn.path).query)
    assert q["mode"] == ["translate"]
    assert q["language_code"] == ["hi-IN"]
    assert q["model"] == ["saaras:v3-realtime"]
    assert q["endpointing"] == ["manual"]


async def test_model_is_configurable():
    async def handler(conn: Conn) -> None:
        await _utterance(conn, "x", "y")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = SarvamSTTTranslate(api_key="k", base_url=srv.url, model="saaras:v4")
        stream = await stt.open("kn-IN")
        await stream.push(F1)
        assert await stream.end() == "y"
        await stream.aclose()
        await stt.aclose()
    q = parse_qs(urlparse(srv.conns[0].path).query)
    assert q["model"] == ["saaras:v4"] and q["mode"] == ["translate"]
