import asyncio
from urllib.parse import parse_qs, urlparse

from dubhash.interp.contracts import STT, STTStream
from dubhash.providers.deepgram_stt import DeepgramSTT
from tests.providers.wsmock import Conn, ws_server

F1, F2 = b"\x01\x00" * 320, b"\x02\x00" * 320


def _results(text: str, *, is_final: bool, speech_final: bool = False, from_finalize=False):
    return {
        "type": "Results",
        "channel_index": [0, 1],
        "duration": 1.0,
        "start": 0.0,
        "is_final": is_final,
        "speech_final": speech_final,
        "from_finalize": from_finalize,
        "channel": {"alternatives": [{"transcript": text, "confidence": 0.9, "words": []}]},
        "metadata": {"request_id": "r", "model_info": {}, "model_uuid": "u"},
    }


async def _utterance(conn: Conn, *finals: str) -> None:
    await conn.recv_until(lambda m: isinstance(m, bytes))
    await conn.send(_results("main gate", is_final=False))
    await conn.recv_until(lambda m: isinstance(m, dict) and m.get("type") == "Finalize")
    for i, t in enumerate(finals):
        last = i == len(finals) - 1
        await conn.send(_results(t, is_final=True, from_finalize=last))


def _dg(srv, **kw) -> DeepgramSTT:
    kw.setdefault("final_timeout_s", 1.0)
    return DeepgramSTT(api_key="dg-test", base_url=srv.url, **kw)


async def test_streams_binary_audio_and_returns_finalized_transcript():
    async def handler(conn: Conn) -> None:
        await _utterance(conn, "main gate ke", "bahar hoon")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _dg(srv)
        assert isinstance(stt, STT) and stt.name == "deepgram"
        s = await stt.open("hi-IN")
        assert isinstance(s, STTStream)
        await s.push(F1)
        await s.push(F2)
        assert await s.end() == "main gate ke bahar hoon"
        assert [p async for p in s.partials()] == ["main gate"]
        await stt.aclose()

    conn = srv.conns[0]
    assert conn.headers["authorization"] == "Token dg-test"
    url = urlparse(conn.path)
    assert url.path == "/v1/listen"
    q = parse_qs(url.query)
    assert q["model"] == ["nova-3"]
    assert q["language"] == ["hi"]
    assert q["encoding"] == ["linear16"]
    assert q["sample_rate"] == ["16000"]
    assert q["channels"] == ["1"]
    assert q["interim_results"] == ["true"]
    assert [m for m in conn.received if isinstance(m, bytes)] == [F1, F2]


async def test_warm_socket_reused_and_keepalive_sent_while_idle():
    pinged = asyncio.Event()

    async def handler(conn: Conn) -> None:
        await _utterance(conn, "one")
        await conn.recv_until(lambda m: isinstance(m, dict) and m.get("type") == "KeepAlive")
        pinged.set()
        await _utterance(conn, "two")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _dg(srv, keepalive_s=0.01)
        s = await stt.open("en-IN")
        await s.push(F1)
        assert await s.end() == "one"
        await asyncio.wait_for(pinged.wait(), 1.0)
        s = await stt.open("en-IN")
        await s.push(F2)
        assert await s.end() == "two"
        await stt.aclose()

    assert len(srv.conns) == 1
    assert parse_qs(urlparse(srv.conns[0].path).query)["language"] == ["en-IN"]


async def test_speech_final_sets_eou_signal():
    async def handler(conn: Conn) -> None:
        await conn.recv_until(lambda m: isinstance(m, bytes))
        await conn.send(_results("haan", is_final=True, speech_final=True))
        await conn.recv_until(lambda m: isinstance(m, dict) and m.get("type") == "Finalize")
        await conn.send(_results("", is_final=True, from_finalize=True))
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _dg(srv)
        s = await stt.open("hi-IN")
        await s.push(F1)
        await asyncio.wait_for(s.eou_signal.wait(), 1.0)
        assert await s.end() == "haan"
        await stt.aclose()


async def test_end_times_out_with_empty_text():
    async def handler(conn: Conn) -> None:
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _dg(srv, final_timeout_s=0.05)
        s = await stt.open("hi-IN")
        await s.push(F1)
        assert await s.end() == ""
        await stt.aclose()


async def test_reconnects_and_replays_when_socket_dies():
    async def handler(conn: Conn) -> None:
        if len(srv.conns) == 1:
            await conn.recv_until(lambda m: isinstance(m, bytes))
            await conn.ws.close(code=1011)
            return
        await _utterance(conn, "again")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _dg(srv)
        s = await stt.open("hi-IN")
        await s.push(F1)
        await asyncio.sleep(0.02)
        await s.push(F2)
        assert await s.end() == "again"
        await stt.aclose()

    assert [m for m in srv.conns[1].received if isinstance(m, bytes)] == [F1, F2]
