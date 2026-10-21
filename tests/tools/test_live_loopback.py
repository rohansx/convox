"""tools/live_loopback.py offline: the real app and CallSession with fake providers."""

from __future__ import annotations

import json

import pytest

from dubhash.audio.earcon import EARCON
from dubhash.audio.frames import FRAME_BYTES
from tools import live_loopback as ll


def test_measure_splits_earcon_from_speech():
    tone = b"\x00\x10" * (FRAME_BYTES // 2)
    rec = ll.SILENCE * 10 + ll.SILENCE * 5 + EARCON + ll.SILENCE * 2 + tone * 4 + ll.SILENCE * 3
    row = ll.TurnRow(0, "a", "x", n_start=0, n_voice_end=10, n_next=len(rec) // FRAME_BYTES)
    ll.measure(row, rec)
    assert row.earcon_ms == 100
    assert row.heard_ms == 100 + 120 + 40
    assert row.heard_audio_ms == 120 + 80


def test_join_server_by_speech_end_window():
    rows = [
        ll.TurnRow(0, "a", "x", n_start=0, n_voice_end=50, n_next=200),
        ll.TurnRow(1, "b", "y", n_start=200, n_voice_end=260, n_next=400),
    ]
    turns = [
        {"side": "b", "t_speech_end": 4200, "backchannel": False},
        {"side": "a", "t_speech_end": 900, "backchannel": False},
        {"side": "a", "t_speech_end": 950, "backchannel": True},
    ]
    ll.join_server(rows, turns, offset_ms=100.0)
    assert [t["t_speech_end"] for t in rows[0].server] == [900]
    assert [t["t_speech_end"] for t in rows[1].server] == [4200]


@pytest.fixture
def server():
    with ll.serve({"dubhash_env": "test"}, None) as srv:
        yield srv


async def test_core_loop_with_fakes(server, tmp_path):
    utts = ll.fake_utts(ll.SCRIPT[:2])
    opts = ll.Opts(opening="skip", pause_ms=300, quiet_ms=400, turn_timeout_s=6)
    result = await ll.run_call(server, utts, opts)

    assert result["status"] == "completed"
    assert result["opening"]["received"]
    rows = result["rows"]
    assert [r.side for r in rows] == ["a", "b"]
    for r in rows:
        assert r.earcon_ms is not None and r.heard_ms is not None, r
        assert r.heard_ms >= r.earcon_ms + 120
        assert r.heard_audio_ms > 300  # the fake translation's tone, not one stray frame
        assert len(r.server) == 1, result["turns"]
        st = ll.stages(r.server[0])
        assert all(v is not None and v >= 0 for v in st.values()), st
        # the listener hears the earcon just after the server starts playing it
        assert r.play_to_ear_ms is not None and -25 <= r.play_to_ear_ms <= 80, r
        assert r.vad_end_lag_ms is not None
        assert r.server[0]["src_text"] and r.server[0]["tgt_text"]  # record=true
    assert result["acks"]["a"] >= 1 and result["acks"]["b"] >= 1

    summary = ll.summarize(result)
    assert summary["heard"]["n"] == 2 and summary["first_audio"]["n"] == 2
    path = ll.write_outputs(result, summary, tmp_path)
    doc = json.loads(path.read_text())
    assert doc["summary"]["heard"]["p50"] is not None
    assert (tmp_path / f"{result['call_id']}_a.wav").stat().st_size > 44
    assert "p50" in ll.table(result, summary)


async def test_ticker_behind_schedule_still_yields():
    """A ticker that falls behind must not starve the websocket readers (it once spun)."""
    import asyncio
    import time

    class Slow:
        async def tick(self):
            time.sleep(0.03)  # noqa: ASYNC251 - every tick overruns its 20 ms slot

    ticker = ll.Ticker([Slow()])
    task = asyncio.create_task(ticker.run())
    progressed = 0
    for _ in range(5):
        await asyncio.sleep(0)
        progressed += 1
    await ticker.wait_ticks(3)
    task.cancel()
    assert progressed == 5 and ticker.max_late_ms > 0


def test_stages_are_critical_path_when_early_final_finishes_before_eou():
    # early final: STT final and MT land inside the 550 ms EOU wait
    turn = dict(
        t_speech_end=1000,
        t_eou=1550,
        t_stt_final=1190,
        t_mt=1520,
        t_tts_ttfb=1700,
        t_play_start=1710,
    )
    st = ll.stages(turn)
    assert st == {"eou": 550, "stt": 0, "mt": 0, "tts": 150, "queue": 10, "first_audio": 710}
    assert st["eou"] + st["stt"] + st["mt"] + st["tts"] + st["queue"] == st["first_audio"]


def test_stages_unchanged_for_sequential_pipeline():
    turn = dict(
        t_speech_end=0, t_eou=550, t_stt_final=840, t_mt=1540, t_tts_ttfb=1790, t_play_start=1800
    )
    assert ll.stages(turn) == {
        "eou": 550,
        "stt": 290,
        "mt": 700,
        "tts": 250,
        "queue": 10,
        "first_audio": 1800,
    }


async def test_join_path_uses_the_qr_token_sockets(server):
    """--join: the call is started from the host page (POST /join) and both tabs connect
    with the token from the QR URLs, like the stage phones."""
    utts = ll.fake_utts(ll.script_for("mr-IN", "hi-IN")[:2])
    opts = ll.Opts(
        a_lang="mr-IN", b_lang="hi-IN", join=True, opening="skip", pause_ms=300,
        quiet_ms=400, turn_timeout_s=6,
    )  # fmt: skip
    result = await ll.run_call(server, utts, opts)
    assert result["status"] == "completed"
    assert result["pair"] == "mr-hi"
    assert all("&t=" in u for u in result["sockets"].values()), result["sockets"]
    assert [r.side for r in result["rows"]] == ["a", "b"]
    assert all(r.heard_ms is not None for r in result["rows"])


def test_scripts_and_voices_follow_the_languages():
    mr = ll.script_for("mr-IN", "hi-IN")
    assert len(mr) >= 6 and mr[0].side == "a" and mr[1].side == "b"
    assert ll.script_for("hi-IN", "kn-IN") == ll.SCRIPT
    assert [ln.side for ln in ll.script_for("mr-IN", "en-IN")] == ["a", "b"] * 3
    assert ll.voice_for("a", "mr-IN") == "bulbul:mr-male-1"
    assert ll.voice_for("b", "hi-IN") == "bulbul:hi-female-1"
