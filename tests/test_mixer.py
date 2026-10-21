import asyncio
from collections.abc import AsyncIterator

import numpy as np
import pytest

from dubhash.audio.earcon import EARCON
from dubhash.audio.frames import FRAME_BYTES
from dubhash.audio.mixer import RenderStats, db_to_gain, mix, passthrough_gain, render_playback


def _const(value: int, samples: int) -> bytes:
    return np.full(samples, value, dtype=np.int16).tobytes()


def _arr(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.int16)


# --- db_to_gain / mix ---------------------------------------------------------


def test_db_to_gain():
    assert db_to_gain(0.0) == 1.0
    assert db_to_gain(-14.0) == pytest.approx(0.1995, abs=1e-4)
    assert db_to_gain(-6.0) == pytest.approx(0.501, abs=1e-3)


def test_mix_ducks_overlay_by_14db():
    base = _const(0, 320)
    overlay = _const(10000, 320)
    out, clipped = mix(base, overlay, -14.0)
    assert not clipped
    ratio = _arr(out).astype(float) / 10000
    assert np.allclose(ratio, 0.1995, atol=1e-3)


def test_mix_adds_base_at_unity():
    out, clipped = mix(_const(1000, 320), _const(-5000, 320), -14.0)
    assert not clipped
    assert np.all(_arr(out) == 1000 + round(-5000 * db_to_gain(-14.0)))


def test_mix_saturates_full_scale_without_wrap():
    out, clipped = mix(_const(32767, 320), _const(32767, 320), -14.0)
    assert clipped
    assert np.all(_arr(out) == 32767)
    out, clipped = mix(_const(-32768, 320), _const(-32768, 320), -14.0)
    assert clipped
    assert np.all(_arr(out) == -32768)


def test_mix_output_length_equals_base():
    base = _const(100, 320)
    out, _ = mix(base, _const(100, 100), 0.0)  # overlay shorter: zero-padded
    a = _arr(out)
    assert len(out) == len(base)
    assert np.all(a[:100] == 200) and np.all(a[100:] == 100)
    out, _ = mix(base, _const(100, 1000), 0.0)  # overlay longer: truncated
    assert len(out) == len(base)


def test_mix_empty_overlay_is_identity():
    base = _const(1234, 320)
    assert mix(base, b"", -14.0) == (base, False)


def test_passthrough_gain():
    f = _const(1000, 320)
    assert passthrough_gain(f) == f
    assert np.all(_arr(passthrough_gain(f, -6.0)) == round(1000 * db_to_gain(-6.0)))
    assert np.all(_arr(passthrough_gain(_const(30000, 320), 6.0)) == 32767)


# --- earcon -------------------------------------------------------------------


def test_earcon_is_six_frames_soft_and_faded():
    assert len(EARCON) == 6 * FRAME_BYTES
    x = _arr(EARCON).astype(float) / 32768
    peak = np.abs(x).max()
    assert 0.1 < peak <= 10 ** (-12 / 20) + 1e-3  # about -12 dBFS peak
    assert abs(x[0]) < 0.01 and abs(x[-1]) < 0.01  # fades in and out


# --- render_playback ----------------------------------------------------------


async def _chunks(*chunks: bytes) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


async def _collect(it: AsyncIterator[bytes]) -> list[bytes]:
    return [f async for f in it]


async def test_render_earcon_first_then_tts_reframed():
    tts = _const(500, 1000)  # 2000 bytes: 3 full frames + 80-byte tail
    frames = await _collect(render_playback(_chunks(tts[:700], tts[700:])))
    assert all(len(f) == FRAME_BYTES for f in frames)
    assert b"".join(frames[:6]) == EARCON
    body = b"".join(frames[6:])
    assert len(frames) == 6 + 4
    assert body[: len(tts)] == tts
    assert body[len(tts) :] == b"\x00" * (4 * FRAME_BYTES - len(tts))  # last frame zero-padded


async def test_render_without_earcon():
    frames = await _collect(render_playback(_chunks(_const(700, 320)), earcon=None))
    assert frames == [_const(700, 320)]


async def test_render_ducks_source_under_tts_and_stops_with_tts():
    tts = _const(1000, 640)  # 2 frames
    source = _const(10000, 320 * 5)  # longer than tts: cut at tts end
    stats = RenderStats()
    frames = await _collect(
        render_playback(_chunks(tts), source_audio=source, earcon=None, stats=stats)
    )
    assert len(frames) == 2
    expected = 1000 + round(10000 * db_to_gain(-14.0))
    assert all(np.all(_arr(f) == expected) for f in frames)
    assert stats == RenderStats(frames=2, clipped=False)


async def test_render_source_shorter_than_tts_only_overlaps_start():
    tts = _const(1000, 640)
    source = _const(10000, 100)
    frames = await _collect(render_playback(_chunks(tts), source_audio=source, earcon=None))
    a = _arr(b"".join(frames))
    assert np.all(a[:100] != 1000) and np.all(a[100:] == 1000)


async def test_render_source_aligned_to_tts_not_earcon():
    source = _const(10000, 320)
    frames = await _collect(
        render_playback(_chunks(_const(0, 320)), source_audio=source, trim_lead_ms=0)
    )
    assert b"".join(frames[:6]) == EARCON
    assert np.all(_arr(frames[6]) == round(10000 * db_to_gain(-14.0)))


async def test_render_records_clipping_and_frame_count():
    stats = RenderStats()
    frames = await _collect(
        render_playback(
            _chunks(_const(32767, 320)),
            source_audio=_const(32767, 320),
            duck_db=0.0,
            stats=stats,
        )
    )
    assert np.all(_arr(frames[-1]) == 32767)
    assert stats.clipped is True
    assert stats.frames == 7  # earcon frames count too


async def test_render_empty_tts_yields_only_earcon():
    frames = await _collect(render_playback(_chunks(), source_audio=_const(5, 320)))
    assert b"".join(frames) == EARCON


async def test_render_streams_before_tts_finishes():
    release = asyncio.Event()

    async def gated() -> AsyncIterator[bytes]:
        yield _const(100, 320)
        await release.wait()
        yield _const(200, 320)

    it = render_playback(gated(), earcon=None)
    first = await asyncio.wait_for(anext(it), timeout=0.05)
    assert first == _const(100, 320)
    assert not release.is_set()
    release.set()
    rest = [f async for f in it]
    assert rest == [_const(200, 320)]


# --- leading TTS silence (live: Bulbul starts every clip with 100-320 ms of zeros) -------


async def test_render_trims_leading_digital_silence_of_tts():
    voice = _const(900, 640)
    tts = _chunks(b"\x00\x00" * 2400, b"\x00\x00" * 800 + voice)  # 200 ms of zeros
    frames = await _collect(render_playback(tts, earcon=None))
    assert b"".join(frames) == voice  # starts with the voice, nothing lost after it


async def test_render_trim_is_capped_and_keeps_silence_inside_speech():
    voice = _const(900, 320)
    lead = b"\x00\x00" * (16 * 600)  # 600 ms of zeros, more than the 400 ms cap
    gap = b"\x00\x00" * 320
    frames = await _collect(render_playback(_chunks(lead + voice + gap + voice), earcon=None))
    body = b"".join(frames)
    assert body == b"\x00\x00" * (16 * 200) + voice + gap + voice


async def test_render_ducked_source_aligned_to_first_kept_tts_sample():
    source = _const(10000, 320)
    tts = _chunks(b"\x00\x00" * 320 + _const(100, 320))
    frames = await _collect(render_playback(tts, source_audio=source, earcon=None))
    assert len(frames) == 1
    assert np.all(_arr(frames[0]) == 100 + round(10000 * db_to_gain(-14.0)))


async def test_render_trim_can_be_turned_off():
    tts = b"\x00\x00" * 320 + _const(100, 320)
    frames = await _collect(render_playback(_chunks(tts), earcon=None, trim_lead_ms=0))
    assert b"".join(frames) == tts


async def test_render_trims_near_silent_lead_in_too():
    """Live (Bulbul kn): a few +-1..5 samples, then zeros, then the voice."""
    voice = _const(900, 320)
    lead = np.zeros(16 * 150, dtype=np.int16)
    lead[:40] = np.tile([1, -1, 3, -5], 10)
    frames = await _collect(render_playback(_chunks(lead.tobytes() + voice), earcon=None))
    assert b"".join(frames) == voice
