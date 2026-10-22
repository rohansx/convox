import asyncio
import base64
import logging
from urllib.parse import parse_qs, urlparse

from convox.interp.contracts import STT, STTStream
from convox.providers.sarvam_stt import SarvamSTT
from tests.providers.wsmock import Conn, ws_server

F1, F2, F3 = b"\x01\x00" * 320, b"\x02\x00" * 320, b"\x03\x00" * 320


def _audio(m) -> bytes | None:
    if isinstance(m, dict) and m.get("event") == "audio_input":
        return base64.b64decode(m["audio"])
    return None


def _events(conn: Conn) -> list[str]:
    return [m["event"] for m in conn.received if isinstance(m, dict)]


async def _manual_utterance(conn: Conn, text: str, partials: tuple[str, ...] = ()) -> None:
    await conn.recv_until(lambda m: m.get("event") == "speech_start")
    for p in partials:
        await conn.send({"event": "transcript.partial", "utterance_idx": 0, "text": p})
    await conn.recv_until(lambda m: m.get("event") == "speech_end")
    await conn.send({"event": "transcript.final", "utterance_idx": 0, "text": text})


def _stt(srv, **kw) -> SarvamSTT:
    kw.setdefault("final_timeout_s", 1.0)
    return SarvamSTT(api_key="sk-test", base_url=srv.url, **kw)


async def test_manual_utterance_streams_audio_and_returns_final():
    async def handler(conn: Conn) -> None:
        await conn.send({"event": "session.begin", "request_id": "r1"})
        await _manual_utterance(conn, "namaste aap kaise hain", partials=("namaste",))
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        assert isinstance(stt, STT) and stt.name == "sarvam"
        stream = await stt.open("hi-IN")
        assert isinstance(stream, STTStream)
        await stream.push(F1)
        await stream.push(F2)
        final = await stream.end()
        partials = [p async for p in stream.partials()]
        await stream.aclose()
        await stt.aclose()

    assert final == "namaste aap kaise hain"
    assert partials == ["namaste"]
    conn = srv.conns[0]
    assert conn.headers["api-subscription-key"] == "sk-test"
    url = urlparse(conn.path)
    assert url.path == "/speech-to-text-realtime/ws"
    q = parse_qs(url.query)
    assert q["language_code"] == ["hi-IN"]
    assert q["model"] == ["saaras:v3-realtime"]
    assert q["endpointing"] == ["manual"]
    assert q["encoding"] == ["linear16"]
    assert q["sample_rate"] == ["16000"]
    assert q["stream_type"] == ["fast"]
    assert [a for m in conn.received if (a := _audio(m))] == [F1, F2]
    assert _events(conn) == ["speech_start", "audio_input", "audio_input", "speech_end"]


async def test_warm_socket_is_reused_for_the_next_utterance():
    async def handler(conn: Conn) -> None:
        await _manual_utterance(conn, "one")
        await _manual_utterance(conn, "two")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        outs = []
        for frame in (F1, F2):
            s = await stt.open("kn-IN")
            await s.push(frame)
            outs.append(await s.end())
            await s.aclose()
        await stt.aclose()

    assert outs == ["one", "two"]
    assert len(srv.conns) == 1


async def test_end_times_out_with_empty_text_and_drops_the_socket(caplog):
    async def handler(conn: Conn) -> None:
        if len(srv.conns) == 1:
            await conn.ws.wait_closed()  # never finalises
        else:
            await _manual_utterance(conn, "fresh")
            await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv, final_timeout_s=0.05)
        s = await stt.open("hi-IN")
        await s.push(F1)
        with caplog.at_level(logging.WARNING):
            assert await s.end() == ""
        assert any("stt.final_timeout" in r.message for r in caplog.records)
        s2 = await stt.open("hi-IN")
        await s2.push(F2)
        assert await s2.end() == "fresh"
        await stt.aclose()

    assert len(srv.conns) == 2


async def test_socket_killed_mid_utterance_reconnects_and_replays_audio():
    async def handler(conn: Conn) -> None:
        if len(srv.conns) == 1:
            await conn.recv_until(lambda m: _audio(m) == F2)
            await conn.ws.close(code=1011)
            return
        await _manual_utterance(conn, "recovered")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        s = await stt.open("hi-IN")
        await s.push(F1)
        await s.push(F2)
        await asyncio.sleep(0.02)  # let the close land
        await s.push(F3)
        assert await s.end() == "recovered"
        await stt.aclose()

    assert len(srv.conns) == 2
    assert [a for m in srv.conns[1].received if (a := _audio(m))] == [F1, F2, F3]


async def test_socket_killed_while_awaiting_final_reconnects():
    async def handler(conn: Conn) -> None:
        if len(srv.conns) == 1:
            await conn.recv_until(lambda m: m.get("event") == "speech_end")
            await conn.ws.close(code=1011)
            return
        await _manual_utterance(conn, "late but here")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        s = await stt.open("hi-IN")
        await s.push(F1)
        assert await s.end() == "late but here"
        await stt.aclose()

    assert [a for m in srv.conns[1].received if (a := _audio(m))] == [F1]


async def test_idle_warm_socket_gets_keepalive_pings():
    got_ping = asyncio.Event()

    async def handler(conn: Conn) -> None:
        await _manual_utterance(conn, "x")
        await conn.recv_until(lambda m: m.get("event") == "ping")
        got_ping.set()
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv, keepalive_s=0.01)
        s = await stt.open("hi-IN")
        await s.push(F1)
        await s.end()
        await asyncio.wait_for(got_ping.wait(), 1.0)
        await stt.aclose()


async def test_prewarm_opens_a_socket_that_open_then_uses():
    async def handler(conn: Conn) -> None:
        await _manual_utterance(conn, "warm")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        await stt.prewarm("hi-IN")
        assert len(srv.conns) == 1
        s = await stt.open("hi-IN")
        await s.push(F1)
        assert await s.end() == "warm"
        await stt.aclose()

    assert len(srv.conns) == 1


async def test_closing_an_unended_stream_settles_it_and_keeps_the_socket_warm():
    """A noise blip the interpreter discards is aclose()d without end() (L10: it cost the
    side's next utterance a 207 ms reconnect live). The stream is ended in the background
    and its socket goes back to the pool once the server's final settles it."""

    async def handler(conn: Conn) -> None:
        await _manual_utterance(conn, "")
        await _manual_utterance(conn, "agla")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        s = await stt.open("hi-IN")
        await s.push(F1)
        await s.aclose()
        for _ in range(100):
            if stt._idle.get("hi-IN"):
                break
            await asyncio.sleep(0.01)
        s2 = await stt.open("hi-IN")
        await s2.push(F2)
        assert await s2.end() == "agla"
        await stt.aclose()
    assert len(srv.conns) == 1


async def test_pool_close_stops_a_settling_stream():
    async def handler(conn: Conn) -> None:
        await conn.recv_until(lambda m: m.get("event") == "speech_end")
        await conn.ws.wait_closed()  # never sends the final

    async with ws_server(handler) as srv:
        stt = _stt(srv, final_timeout_s=5.0)
        s = await stt.open("hi-IN")
        await s.push(F1)
        await s.aclose()
        await stt.aclose()
        assert not stt._settling and not stt._idle.get("hi-IN")


async def test_prewarm_replaces_an_idle_socket_the_server_closed():
    """Next-turn prewarm (L10) must not trust a pooled socket that has died meanwhile
    (Sarvam closes idle sockets with 1008): it connects a fresh one."""
    first = asyncio.Event()

    async def handler(conn: Conn) -> None:
        if not first.is_set():
            first.set()
            await conn.ws.close(1008, "idle")
            return
        await _manual_utterance(conn, "fresh")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        await stt.prewarm("hi-IN")
        for _ in range(50):
            if all(c.dead.is_set() for c in stt._idle.get("hi-IN", [])):
                break
            await asyncio.sleep(0.01)
        await stt.prewarm("hi-IN")
        assert len(srv.conns) == 2
        idle = stt._idle["hi-IN"]
        assert len(idle) == 1 and not idle[0].dead.is_set()
        s = await stt.open("hi-IN")
        await s.push(F1)
        assert await s.end() == "fresh"
        await stt.aclose()


async def test_vad_mode_sets_eou_signal_and_joins_finals():
    async def handler(conn: Conn) -> None:
        await conn.recv_until(lambda m: _audio(m) == F1)
        await conn.send({"event": "vad.speech_start", "utterance_idx": 0})
        await conn.send({"event": "transcript.partial", "utterance_idx": 0, "text": "bhaiya"})
        await conn.send({"event": "vad.speech_end", "utterance_idx": 0})
        await conn.send({"event": "transcript.final", "utterance_idx": 0, "text": "bhaiya main"})
        await conn.send({"event": "vad.speech_start", "utterance_idx": 1})
        await conn.send({"event": "vad.speech_end", "utterance_idx": 1})
        await conn.recv_until(lambda m: _audio(m) == F2)
        await conn.send({"event": "transcript.final", "utterance_idx": 1, "text": "gate pe hoon"})
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv, endpointing="vad")
        s = await stt.open("hi-IN")
        await s.push(F1)
        await asyncio.wait_for(s.eou_signal.wait(), 1.0)
        await s.push(F2)
        assert await s.end() == "bhaiya main gate pe hoon"
        await stt.aclose()

    q = parse_qs(urlparse(srv.conns[0].path).query)
    assert q["endpointing"] == ["vad"]
    assert "speech_start" not in _events(srv.conns[0])
    assert "speech_end" not in _events(srv.conns[0])


async def test_aclose_before_end_does_not_return_socket_to_pool():
    async def handler(conn: Conn) -> None:
        if len(srv.conns) == 2:
            await _manual_utterance(conn, "second")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        s = await stt.open("hi-IN")
        await s.push(F1)
        await s.aclose()
        assert [p async for p in s.partials()] == []
        s2 = await stt.open("hi-IN")
        await s2.push(F2)
        assert await s2.end() == "second"
        await stt.aclose()

    assert len(srv.conns) == 2


async def test_odia_uses_or_in_on_the_realtime_socket():
    async def handler(conn: Conn) -> None:
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        s = await stt.open("od-IN")
        await s.aclose()
        await stt.aclose()

    assert parse_qs(urlparse(srv.conns[0].path).query)["language_code"] == ["or-IN"]


# --- early final (L3): end a stream at ~200 ms silence, reopen if speech resumes ---------


def _utterance_audio(conn: Conn, n: int) -> list[bytes]:
    """Audio of the n-th utterance (0-based) on a connection, as received."""
    out: list[list[bytes]] = []
    for m in conn.received:
        if isinstance(m, dict) and m.get("event") == "speech_start":
            out.append([])
        elif (a := _audio(m)) is not None:
            out[-1].append(a)
    return out[n]


async def test_early_final_restart_reuses_the_warm_socket_quickly():
    async def handler(conn: Conn) -> None:
        await _manual_utterance(conn, "ek do")
        await _manual_utterance(conn, "ek do teen chaar")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        early = await stt.open("hi-IN")
        await early.push(F1)
        assert await early.end() == "ek do"
        await early.aclose()
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        restarted = await stt.open("hi-IN")  # the socket went back to the pool on end()
        reopen_s = loop.time() - t0
        for f in (F1, F2, F3):  # replay the buffered utterance, then continue live
            await restarted.push(f)
        assert await restarted.end() == "ek do teen chaar"
        await restarted.aclose()
        await stt.aclose()

    assert len(srv.conns) == 1
    assert reopen_s < 0.05
    assert _utterance_audio(srv.conns[0], 1) == [F1, F2, F3]


async def test_restart_while_the_early_final_is_pending_uses_a_second_socket():
    """Speech resumes before the early final lands: the pipeline lets end() finish in the
    background and opens a new stream at once; both sockets end up warm in the pool."""
    release = asyncio.Event()

    async def handler(conn: Conn) -> None:
        if len(srv.conns) == 1:
            await conn.recv_until(lambda m: m.get("event") == "speech_end")
            await release.wait()
            await conn.send({"event": "transcript.final", "text": "ek do"})
        else:
            await _manual_utterance(conn, "ek do teen chaar")
        await conn.recv_until(lambda m: m.get("event") == "speech_start")  # next utterance
        await conn.recv_until(lambda m: m.get("event") == "speech_end")
        await conn.send({"event": "transcript.final", "text": "next"})
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        early = await stt.open("hi-IN")
        await early.push(F1)
        ending = asyncio.create_task(early.end())
        await asyncio.sleep(0.01)
        restarted = await stt.open("hi-IN")
        for f in (F1, F2):
            await restarted.push(f)
        assert await restarted.end() == "ek do teen chaar"
        release.set()
        assert await ending == "ek do"
        await early.aclose()
        await restarted.aclose()
        nxt = await stt.open("hi-IN")  # warm: no third connection
        await nxt.push(F3)
        assert await nxt.end() == "next"
        await stt.aclose()

    assert len(srv.conns) == 2
    assert _utterance_audio(srv.conns[1], 0) == [F1, F2]


async def test_final_language_code_is_kept_as_detected_lang():
    """Sarvam may report the language it heard on a final; the session's cross-talk gate
    drops a final in the other side's language (co-located phones)."""

    async def handler(conn: Conn) -> None:
        for data in ({"language_code": "mr-IN"}, None):
            await conn.recv_until(lambda m: m.get("event") == "speech_start")
            await conn.recv_until(lambda m: m.get("event") == "speech_end")
            msg = {"event": "transcript.final", "utterance_idx": 0, "text": "ho"}
            if data:
                msg["data"] = data
            await conn.send(msg)
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        langs = []
        for _ in range(2):
            stream = await stt.open("hi-IN")
            assert stream.detected_lang is None
            await stream.push(F1)
            await stream.end()
            langs.append(stream.detected_lang)
            await stream.aclose()
        await stt.aclose()
    assert langs == ["mr-IN", None]


async def test_auto_language_reports_detected_lang_and_confidence_on_the_final():
    """language_code=auto (measured live 21 Oct): finals carry `language` and
    `language_confidence`; partials carry `language` too but it is unreliable (en-IN while
    Marathi is spoken), so only the final sets detected_lang."""

    async def handler(conn: Conn) -> None:
        await conn.recv_until(lambda m: m.get("event") == "speech_start")
        await conn.send(
            {"event": "transcript.partial", "utterance_idx": 0, "text": "Mala", "language": "en-IN"}
        )
        await conn.recv_until(lambda m: m.get("event") == "speech_end")
        await conn.send(
            {
                "event": "transcript.final",
                "utterance_idx": 0,
                "text": "मला उद्या",
                "language": "mr-IN",
                "language_confidence": 0.98,
            }
        )
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        stream = await stt.open("auto")
        assert stream.detected_lang is None and stream.detected_confidence is None
        await stream.push(F1)
        await asyncio.sleep(0.05)
        assert stream.detected_lang is None  # a partial's language is ignored
        assert await stream.end() == "मला उद्या"
        assert stream.detected_lang == "mr-IN"
        assert stream.detected_confidence == 0.98
        await stream.aclose()
        await stt.aclose()
    assert parse_qs(urlparse(srv.conns[0].path).query)["language_code"] == ["auto"]


async def test_auto_language_normalises_odia_to_od_in():
    async def handler(conn: Conn) -> None:
        await conn.recv_until(lambda m: m.get("event") == "speech_end")
        await conn.send(
            {
                "event": "transcript.final",
                "text": "x",
                "language": "or-IN",
                "language_confidence": 0.9,
            }
        )
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        stt = _stt(srv)
        stream = await stt.open("auto")
        await stream.push(F1)
        await stream.end()
        assert stream.detected_lang == "od-IN"
        await stream.aclose()
        await stt.aclose()
