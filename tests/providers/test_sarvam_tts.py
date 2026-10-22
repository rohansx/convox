import base64
import struct
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

from convox.interp.contracts import TTS
from convox.providers.registry import ProviderError
from convox.providers.sarvam_tts import BulbulTTS, decode_audio, vendor_voice
from tests.providers.wsmock import Conn, ws_server


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _wav(pcm: bytes, rate: int) -> bytes:
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<I", 16)
        + fmt
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


async def _speak(conn: Conn, chunks: list[bytes], content_type: str = "audio/pcm") -> None:
    """One synth on the vendor side: config, text, flush -> audio..., final."""
    await conn.recv_until(lambda m: m.get("type") == "flush")
    for c in chunks:
        await conn.send({"type": "audio", "data": {"content_type": content_type, "audio": _b64(c)}})
    await conn.send({"type": "event", "data": {"event_type": "final"}})


def _query(conn: Conn) -> dict[str, list[str]]:
    return parse_qs(urlparse(conn.path).query)


async def test_synth_sends_config_text_flush_and_yields_pcm():
    async def handler(conn: Conn) -> None:
        await _speak(conn, [b"\x01\x00" * 100, b"\x02\x00" * 50])
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        tts = BulbulTTS(api_key="sk-test", base_url=srv.url)
        assert isinstance(tts, TTS) and tts.name == "bulbul"
        out = [c async for c in tts.synth("ನಮಸ್ಕಾರ", "bulbul:kn-female-1", "kn-IN")]
        await tts.aclose()

    assert b"".join(out) == b"\x01\x00" * 100 + b"\x02\x00" * 50
    conn = srv.conns[0]
    assert conn.headers["api-subscription-key"] == "sk-test"
    assert urlparse(conn.path).path == "/text-to-speech/ws"
    assert _query(conn)["model"] == ["bulbul:v3"]
    config, text, flush = conn.received[:3]
    assert config["type"] == "config"
    assert config["data"]["language_code"] == "kn-IN"
    assert config["data"]["speaker"] == "ishita"
    assert config["data"]["output_audio_codec"] == "linear16"
    assert config["data"]["speech_sample_rate"] == "16000"
    assert text == {"type": "text", "data": {"text": "ನಮಸ್ಕಾರ"}}
    assert flush == {"type": "flush"}


async def test_wav_chunks_at_other_rate_are_stripped_and_resampled_to_16k():
    pcm22 = (np.sin(np.arange(22050) / 10) * 8000).astype(np.int16).tobytes()  # 1 s

    async def handler(conn: Conn) -> None:
        await _speak(conn, [_wav(pcm22, 22050)], content_type="audio/wav")
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        tts = BulbulTTS(api_key="k", base_url=srv.url)
        out = b"".join([c async for c in tts.synth("x", "bulbul:shubh", "hi-IN")])
        await tts.aclose()

    assert not out.startswith(b"RIFF")
    assert abs(len(out) // 2 - 16000) < 400  # ~1 s at 16k


async def test_warm_socket_is_reused_between_synths():
    async def handler(conn: Conn) -> None:
        await _speak(conn, [b"\x01\x00"])
        await _speak(conn, [b"\x02\x00"])
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        tts = BulbulTTS(api_key="k", base_url=srv.url)
        a = b"".join([c async for c in tts.synth("one", "bulbul:shubh", "kn-IN")])
        b = b"".join([c async for c in tts.synth("two", "bulbul:ishita", "kn-IN")])
        await tts.aclose()

    assert (a, b) == (b"\x01\x00", b"\x02\x00")
    assert len(srv.conns) == 1
    configs = [m for m in srv.conns[0].received if m.get("type") == "config"]
    assert [c["data"]["speaker"] for c in configs] == ["shubh", "ishita"]


async def test_reconnects_when_the_warm_socket_was_closed_by_the_server():
    async def handler(conn: Conn) -> None:
        await _speak(conn, [b"\x05\x00"])
        if len(srv.conns) == 1:
            await conn.ws.close()
        else:
            await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        tts = BulbulTTS(api_key="k", base_url=srv.url)
        [c async for c in tts.synth("one", "bulbul:shubh", "kn-IN")]
        out = b"".join([c async for c in tts.synth("two", "bulbul:shubh", "kn-IN")])
        await tts.aclose()

    assert out == b"\x05\x00"
    assert len(srv.conns) == 2


async def test_vendor_error_raises_provider_error():
    async def handler(conn: Conn) -> None:
        await conn.recv_until(lambda m: m.get("type") == "flush")
        await conn.send({"type": "error", "data": {"message": "bad speaker", "code": 400}})
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        tts = BulbulTTS(api_key="k", base_url=srv.url)
        with pytest.raises(ProviderError):
            [c async for c in tts.synth("x", "bulbul:nobody", "kn-IN")]
        await tts.aclose()


async def test_stalled_vendor_times_out():
    async def handler(conn: Conn) -> None:
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        tts = BulbulTTS(api_key="k", base_url=srv.url, chunk_timeout_s=0.05)
        with pytest.raises(ProviderError):
            [c async for c in tts.synth("x", "bulbul:shubh", "kn-IN")]
        await tts.aclose()


def test_voice_mapping():
    assert vendor_voice("bulbul:kn-female-1") == ("bulbul:v3", "ishita")
    assert vendor_voice("bulbul:ta-male-1") == ("bulbul:v3", "shubh")
    assert vendor_voice("bulbul:gu-male-1") == ("bulbul:v3", "shubh")
    assert vendor_voice("bulbul:gu-female-1") == ("bulbul:v3", "ishita")
    assert vendor_voice("bulbul:priya") == ("bulbul:v3", "priya")
    assert vendor_voice("bulbul:chaitra_kn_conversation") == (
        "bulbul:v4-flash",
        "chaitra_kn_conversation",
    )


def test_decode_audio_variants():
    assert decode_audio("audio/pcm", b"\x01\x00") == (b"\x01\x00", None)
    assert decode_audio("audio/wav", _wav(b"\x01\x00\x02\x00", 24000)) == (
        b"\x01\x00\x02\x00",
        24000,
    )
    with pytest.raises(ProviderError):
        decode_audio("audio/mp3", b"\xff\xfb\x90")


async def test_abandoned_synth_closes_socket_instead_of_reusing_it():
    async def handler(conn: Conn) -> None:
        await _speak(conn, [b"\x01\x00", b"\x02\x00"])
        await conn.ws.wait_closed()

    async with ws_server(handler) as srv:
        tts = BulbulTTS(api_key="k", base_url=srv.url)
        gen = tts.synth("one", "bulbul:shubh", "kn-IN")
        assert await anext(gen) == b"\x01\x00"
        await gen.aclose()  # barge-in: the controller stops consuming
        out = b"".join([c async for c in tts.synth("two", "bulbul:shubh", "kn-IN")])
        await tts.aclose()

    assert out == b"\x01\x00\x02\x00"
    assert len(srv.conns) == 2
