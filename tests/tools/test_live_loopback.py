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


async def test_auto_side_hears_the_menu_and_presses_a_digit():
    """--auto a --menu-digit 5: A's call side is lang "auto"; FakeLID is never sure, so
    after two long lines A's tab hears the keypad menu and presses 5 (Tamil)."""
    # f0 differs per line: Silero stops accepting a fake voice it has heard (fake_utts)
    utts = [
        ll.Utt("a", ll.fake_speech(1400, 120.0), "x"),
        ll.Utt("a", ll.fake_speech(1400, 175.0), "y"),
        ll.Utt("b", ll.fake_speech(1200, 220.0), "z"),
    ]
    with ll.serve({"dubhash_env": "test", "lang_min_speech_ms": 600}, None) as srv:
        opts = ll.Opts(
            a_lang="hi-IN",
            b_lang="kn-IN",
            opening="skip",
            pause_ms=1500,
            quiet_ms=400,
            turn_timeout_s=6,
            auto="a",
            hint="mr-IN",
            menu_digit="5",
        )
        result = await ll.run_call(srv, utts, opts)
    assert result["pair"] == "auto-kn"
    assert result["menu_digits"] == {"a": ["5"]}
    assert result["legs"]["a"]["lang"] == "auto"
    assert result["legs"]["a"]["detected"]["lang"] == "ta-IN"
    assert "lang a=ta-IN (CONFIRMED)" in ll.lang_line(result)


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


async def test_ticker_room_mixes_each_mic_into_the_other_delayed_and_quieter():
    """--room-db: two phones in one room; each mic also hears the other speaker."""
    import asyncio

    import numpy as np

    class Rec:
        def __init__(self, frames):
            self.mic = list(frames)
            self.sent = []

        def take_mic(self):
            return self.mic.pop(0) if self.mic else ll.SILENCE

        async def tick(self, mic=None):
            self.sent.append(mic)

    loud = np.full(FRAME_BYTES // 2, 10000, dtype=np.int16).tobytes()
    a, b = Rec([loud] * 3), Rec([])
    ticker = ll.Ticker([a, b], room_db=-20.0, room_delay_ms=40)
    task = asyncio.create_task(ticker.run())
    await ticker.wait_ticks(6)
    task.cancel()
    vals = [int(np.frombuffer(f, dtype=np.int16)[0]) for f in b.sent[:6]]
    assert vals == [0, 0, 1000, 1000, 1000, 0]  # 2 ticks late, 20 dB down
    assert a.sent[:3] == [loud] * 3  # b's silence adds nothing


async def test_room_cross_talk_makes_no_extra_turns(server):
    utts = ll.fake_utts(ll.SCRIPT[:2])
    opts = ll.Opts(opening="skip", pause_ms=300, quiet_ms=400, turn_timeout_s=6, room_db=-12.0)
    result = await ll.run_call(server, utts, opts)
    spoken = [t for t in result["turns"] if not t.get("backchannel")]
    assert [t["side"] for t in spoken] == ["a", "b"], result["turns"]


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
    utts = ll.fake_utts(ll.SCRIPTS[ll.script_for("mr-IN", "hi-IN")].lines[:2])
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


def test_scripts_cover_the_marathi_pairs():
    assert ll.SCRIPT == ll.SCRIPTS["delivery-hi-kn"].lines
    for key, langs in {
        "delivery-hi-kn": ("hi-IN", "kn-IN"),
        "delivery-mr-hi": ("mr-IN", "hi-IN"),
        "clinic-mr-en": ("mr-IN", "en-IN"),
        "delivery-gu-hi": ("gu-IN", "hi-IN"),
        "clinic-gu-en": ("gu-IN", "en-IN"),
    }.items():
        sc = ll.SCRIPTS[key]
        assert (sc.a_lang, sc.b_lang) == langs
        assert {ln.side for ln in sc.lines} == {"a", "b"}
        assert set(sc.voices) == {"a", "b"}
        assert ll.script_for(*langs) == key
    # the Marathi lines are ours: flagged for a native speaker
    for key in ("delivery-mr-hi", "clinic-mr-en", "delivery-gu-hi", "clinic-gu-en"):
        assert all(ln.review for ln in ll.SCRIPTS[key].lines if ln.side == "a")
    assert ll.script_for("ta-IN", "hi-IN") is None


def test_number_check_against_the_scripted_line():
    row = ll.TurnRow(0, "a", "ओटीपी 4729 आहे, फ्लॅट 302.", n_start=0, n_voice_end=1)
    row.server = [{"src_text": "OTP is 4729, flat 302.", "tgt_text": "ओटीपी 4729 है, फ्लैट 32."}]
    assert ll.numbers_check(row) == "numbers differ: 302 missing in translation"
    row.server[0]["tgt_text"] = "ओटीपी 4729 है, फ्लैट 302."
    assert ll.numbers_check(row) is None
    assert ll.numbers_check(ll.TurnRow(1, "b", "x", n_start=0, n_voice_end=1)) is None


def test_gujarati_scripts_carry_the_numbers_to_check():
    for key in ("delivery-gu-hi", "clinic-gu-en"):
        sc = ll.SCRIPTS[key]
        assert sc.voices["a"] == "bulbul:gu-female-1"
        text = " ".join(ln.text for ln in sc.lines)
        for n in ("302", "4729", "6"):
            assert n in text, (key, n)
