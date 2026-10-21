"""CallRecorder: per-call events, client telemetry, aligned in/out wavs and a summary."""

from __future__ import annotations

import asyncio
import json
import wave
from pathlib import Path

import numpy as np

from dubhash.audio.frames import FRAME_BYTES
from dubhash.clock import FakeClock
from dubhash.events import EventBus
from dubhash.store.recorder import CallRecorder
from dubhash.types import CallEvent

META = {
    "pair": "mr-hi",
    "mode": "interpret",
    "sides": {"a": {"lang": "mr-IN", "stt_lang": "en-IN"}, "b": {"lang": "hi-IN"}},
}


def tone(ms: int, value: int = 1000) -> bytes:
    return np.full(16 * ms, value, dtype=np.int16).tobytes()


def wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getsampwidth() == 2 and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def make(tmp_path: Path, **kw) -> tuple[CallRecorder, FakeClock]:
    clock = FakeClock()
    rec = CallRecorder("c_rec", tmp_path, clock, meta=META, date="2026-10-21", **kw)
    return rec, clock


async def test_writes_every_file_into_dated_call_dir(tmp_path):
    rec, clock = make(tmp_path)
    await rec.start()
    clock.advance(20)
    rec.mic("a", tone(20))
    rec.event(CallEvent("c_rec", clock.now_ms(), "state", {"from": "IDLE", "to": "LISTEN_A"}))
    rec.telemetry("b", {"type": "telemetry", "kind": "play_start", "client_ms": 5})
    await rec.close()
    d = tmp_path / "2026-10-21" / "c_rec"
    assert rec.dir == d
    for name in ("events.jsonl", "client.jsonl", "summary.json"):
        assert (d / name).is_file(), name
    for name in ("a_in", "b_in", "a_out", "b_out"):
        assert (d / f"{name}.wav").is_file(), name
    evs = lines(d / "events.jsonl")
    assert evs[0]["kind"] == "rec.meta" and evs[0]["payload"]["pair"] == "mr-hi"
    st = next(e for e in evs if e["kind"] == "state")
    assert st["ms"] == 20 and st["payload"]["to"] == "LISTEN_A"
    assert st["ts"].endswith("Z") and "T" in st["ts"]
    (c,) = lines(d / "client.jsonl")
    assert c["side"] == "b" and c["rx_ms"] == 20 and c["kind"] == "play_start"


async def test_wavs_share_one_timeline_from_call_start(tmp_path):
    rec, clock = make(tmp_path)
    await rec.start()
    # A speaks from 1000 ms for 200 ms (frames arrive at their end time)
    clock.set(1000)
    for _ in range(10):
        clock.advance(20)
        rec.mic("a", tone(20, 500))
    # B is sent 300 ms of translation at 1500 ms, in one burst (faster than real time)
    clock.set(1500)
    rec.sent("b", tone(100, 700))
    rec.sent("b", tone(200, 700))
    clock.set(3000)
    await rec.close()
    a_in, b_out = wav(rec.dir / "a_in.wav"), wav(rec.dir / "b_out.wav")
    a_out, b_in = wav(rec.dir / "a_out.wav"), wav(rec.dir / "b_in.wav")
    assert len(a_in) == len(b_out) == len(a_out) == len(b_in) == 16 * 3000
    assert not a_in[: 16 * 1000].any() and (a_in[16 * 1000 : 16 * 1200] == 500).all()
    assert not a_in[16 * 1200 :].any()
    assert not b_out[: 16 * 1500].any() and (b_out[16 * 1500 : 16 * 1800] == 700).all()
    assert not b_out[16 * 1800 :].any()


async def test_send_while_still_playing_queues_after_the_previous_audio(tmp_path):
    rec, clock = make(tmp_path)
    await rec.start()
    clock.set(100)
    rec.sent("a", tone(500, 1))
    clock.set(200)  # 400 ms of the first chunk still to play
    rec.sent("a", tone(100, 2))
    clock.set(1000)
    await rec.close()
    out = wav(rec.dir / "a_out.wav")
    assert (out[16 * 100 : 16 * 600] == 1).all() and (out[16 * 600 : 16 * 700] == 2).all()


async def test_clear_drops_audio_that_had_not_played_yet(tmp_path):
    rec, clock = make(tmp_path)
    await rec.start()
    clock.set(100)
    rec.sent("b", tone(1000, 9))
    clock.set(400)
    rec.cleared("b")
    clock.set(2000)
    await rec.close()
    out = wav(rec.dir / "b_out.wav")
    assert (out[16 * 100 : 16 * 400] == 9).all() and not out[16 * 400 :].any()
    clear = next(e for e in lines(rec.dir / "events.jsonl") if e["kind"] == "leg.clear")
    assert clear["payload"] == {"side": "b", "dropped_ms": 700}


async def test_mic_gap_is_padded_with_silence(tmp_path):
    rec, clock = make(tmp_path)
    await rec.start()
    clock.set(20)
    rec.mic("b", tone(20, 3))
    clock.set(2020)  # the phone's socket stalled for 2 s
    rec.mic("b", tone(20, 4))
    await rec.close()
    b_in = wav(rec.dir / "b_in.wav")
    assert (b_in[:320] == 3).all() and not b_in[320 : 16 * 2000].any()
    assert (b_in[16 * 2000 : 16 * 2020] == 4).all()


async def test_flushes_periodically_not_only_at_close(tmp_path):
    rec, clock = make(tmp_path, flush_interval_s=0.01)
    await rec.start()
    clock.set(500)
    rec.mic("a", tone(500))
    rec.note("leg.mark", {"side": "a", "tag": "b:0"})
    for _ in range(100):
        await asyncio.sleep(0.01)
        if (rec.dir / "events.jsonl").exists() and (rec.dir / "a_in.wav").stat().st_size > 44:
            break
    assert any(e["kind"] == "leg.mark" for e in lines(rec.dir / "events.jsonl"))
    await rec.close()


async def test_hot_path_calls_never_touch_the_disk(tmp_path):
    rec, clock = make(tmp_path, flush_interval_s=60)
    await rec.start()
    clock.set(200)
    rec.mic("a", tone(200))
    rec.sent("b", tone(200))
    rec.event(CallEvent("c_rec", 200, "partial", {"side": "a", "text": "x"}))
    assert not (tmp_path / "2026-10-21").exists()
    await rec.close()


async def test_buffer_is_bounded_when_the_disk_is_slow(tmp_path):
    rec, clock = make(tmp_path, flush_interval_s=60, max_buffer_s=1.0)
    await rec.start()
    for _ in range(150):  # 3 s of mic, no flush in between
        clock.advance(20)
        rec.mic("a", tone(20))
    assert rec.buffered_bytes("a_in") <= 16 * 2 * 1000
    await rec.close()
    s = json.loads((rec.dir / "summary.json").read_text())
    assert any(w["code"] == "recording_dropped" for w in s["warnings"])


async def test_records_bus_events_from_before_the_first_leg_attaches(tmp_path):
    bus = EventBus()
    rec, clock = make(tmp_path)
    await rec.start(bus)
    bus.publish(CallEvent("c_rec", 0, "leg", {"side": "a", "status": "attached"}))
    bus.publish(CallEvent("c_other", 0, "leg", {"side": "a", "status": "attached"}))
    clock.set(50)
    bus.publish(CallEvent("c_rec", 50, "call", {"status": "ended", "reason": "hangup"}))
    await asyncio.sleep(0)
    await rec.close()
    kinds = [(e["kind"], e["ms"]) for e in lines(rec.dir / "events.jsonl")]
    assert ("leg", 0) in kinds and ("call", 50) in kinds
    assert len([k for k in kinds if k[0] == "leg"]) == 1


async def test_transcript_text_is_dropped_unless_the_call_records_text(tmp_path):
    rec, _ = make(tmp_path, record_text=False)
    await rec.start()
    rec.event(CallEvent("c_rec", 0, "partial", {"side": "a", "text": "secret"}))
    rec.event(
        CallEvent("c_rec", 0, "turn", {"turn": {"side": "a", "src_text": "s", "tgt_text": "t"}})
    )
    await rec.close()
    raw = (rec.dir / "events.jsonl").read_text()
    assert "secret" not in raw and '"src_text": null' in raw and '"tgt_text": null' in raw


async def test_pong_gives_rtt_and_clock_offset(tmp_path):
    rec, clock = make(tmp_path)
    await rec.start()
    clock.set(5000)
    rec.ping_sent("b", 5000)
    clock.set(5060)
    rec.telemetry(
        "b", {"type": "telemetry", "kind": "pong", "server_ms": 5000, "client_ms": 105_030}
    )
    await rec.close()
    (pong,) = lines(rec.dir / "client.jsonl")
    assert pong["rtt_ms"] == 60 and pong["offset_ms"] == 100_000
    s = json.loads((rec.dir / "summary.json").read_text())
    assert s["clock"]["b"] == {"offset_ms": 100_000, "rtt_ms": 60, "n": 1}


async def test_summary_has_pair_turns_percentiles_and_stages(tmp_path):
    rec, clock = make(tmp_path)
    await rec.start()
    t = {
        "side": "a", "seq": 0, "t_speech_start": 1000, "t_speech_end": 2500, "t_eou": 3050,
        "t_stt_final": 3200, "t_mt": 3400, "t_tts_ttfb": 3700, "t_play_start": 3750,
        "t_play_end": 5750, "backchannel": False,
    }  # fmt: skip
    clock.set(5800)
    rec.event(CallEvent("c_rec", 5800, "turn", {"turn": t}))
    clock.set(9000)
    await rec.close()
    s = json.loads((rec.dir / "summary.json").read_text())
    assert s["call_id"] == "c_rec" and s["pair"] == "mr-hi" and s["mode"] == "interpret"
    assert s["duration_ms"] == 9000 and s["turns"] == 1
    assert s["first_audio"] == {"n": 1, "p50": 1250, "p95": 1250}
    assert s["per_turn"][0]["first_audio_ms"] == 1250
    assert s["stages"]["eou_wait"] == 550 and s["warnings"] == []


async def test_close_is_idempotent_and_late_calls_are_ignored(tmp_path):
    rec, clock = make(tmp_path)
    await rec.start()
    await rec.close()
    rec.mic("a", b"\x00" * FRAME_BYTES)
    rec.event(CallEvent("c_rec", 0, "state", {}))
    await rec.close()
