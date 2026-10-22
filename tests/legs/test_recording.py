"""RecordingLeg: a transparent tap on a LegTransport for the call recorder."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import wave
from pathlib import Path

import numpy as np

from convox.audio.frames import FRAME_BYTES
from convox.clock import FakeClock
from convox.events import EventBus
from convox.legs.base import LegTransport
from convox.legs.fake import FakeLeg
from convox.legs.recording import RecordingLeg
from convox.legs.webrtc import WebRtcLeg
from convox.legs.ws import ws_pair
from convox.store.recorder import CallRecorder
from convox.types import CallEvent
from tests.audio_fixtures import conversation
from tests.test_e2e_fake import make as make_e2e
from tools.bench import drive_lockstep


def lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def recorder(tmp_path: Path, clock: FakeClock) -> CallRecorder:
    return CallRecorder("c_leg", tmp_path, clock, date="d")


async def test_is_a_leg_transport_and_passes_attributes_through(tmp_path):
    clock = FakeClock()
    inner = FakeLeg("b", clock=clock)
    leg = RecordingLeg(inner, recorder(tmp_path, clock), "b")
    assert isinstance(leg, LegTransport)
    assert leg.leg_id == "b" and leg.inner is inner
    assert leg.outbound == b"" and leg.closed is False  # FakeLeg attributes


async def test_taps_frames_send_mark_clear_and_dtmf_without_changing_them(tmp_path):
    clock = FakeClock()
    rec = recorder(tmp_path, clock)
    await rec.start()
    inner = FakeLeg("a", clock=clock, advance_clock=True)
    leg = RecordingLeg(inner, rec, "a")
    marks: list[str] = []
    digits: list[str] = []
    leg.on_mark(marks.append)
    leg.on_dtmf(digits.append)
    frame = np.full(FRAME_BYTES // 2, 77, dtype=np.int16).tobytes()
    inner.feed(frame * 3)
    inner.end_input()
    assert [f async for f in leg.frames()] == [frame] * 3
    await leg.send(b"\x01\x00" * 1600)
    await leg.mark("b:0")
    clock.advance(200)
    inner.poll()
    await leg.clear()
    inner.inject_dtmf("1")
    assert marks == ["b:0"] and digits == ["1"]
    assert [k for k, _ in inner.log] == ["send", "mark", "mark_fired", "clear", "dtmf"]
    await leg.close()
    assert inner.closed
    await rec.close()
    kinds = [(e["kind"], e["payload"]) for e in lines(rec.dir / "events.jsonl")][1:]
    assert kinds == [
        ("leg.mark", {"side": "a", "tag": "b:0"}),
        ("leg.mark_ack", {"side": "a", "tag": "b:0"}),
        ("leg.clear", {"side": "a", "dropped_ms": 0}),
        ("leg.dtmf", {"side": "a", "digit": "1"}),
    ]
    with wave.open(str(rec.dir / "a_in.wav"), "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    assert (pcm[: 3 * FRAME_BYTES // 2] == 77).all()


async def test_webrtc_telemetry_and_pongs_reach_the_recorder(tmp_path):
    clock = FakeClock()
    rec = recorder(tmp_path, clock)
    await rec.start()
    server, client = ws_pair()
    inner = WebRtcLeg("b", server, clock=clock)
    run = asyncio.create_task(inner.run())
    RecordingLeg(inner, rec, "b", ping_interval_s=0.01)
    await asyncio.sleep(0.005)
    ping = next(json.loads(m) for m in client.drain() if isinstance(m, str))
    assert ping["type"] == "ping"
    clock.advance(40)
    pong = {"type": "telemetry", "kind": "pong", "server_ms": ping["server_ms"], "client_ms": 9}
    await client.send_text(json.dumps(pong))
    await client.send_text(json.dumps({"type": "telemetry", "kind": "play_start", "client_ms": 1}))
    await asyncio.sleep(0.005)
    await inner.close()
    await run
    await rec.close()
    got = lines(rec.dir / "client.jsonl")
    assert [c["kind"] for c in got] == ["pong", "play_start"]
    assert got[0]["rtt_ms"] == 40 and all(c["side"] == "b" for c in got)


async def run_e2e(tmp_path: Path | None):
    a_says = ["bhaiya main gate ke bahar hoon", "ek minute ruko"]
    b_says = ["kaunsa building chahiye aapko bataiye", "haan"]
    session, legs, clock, bus, _ = make_e2e(a_says, b_says)
    rec = None
    attach = dict(legs)
    if tmp_path is not None:
        real_bus = EventBus()
        orig = session.bus.publish

        def publish(ev: CallEvent) -> None:
            orig(ev)
            real_bus.publish(ev)

        session.bus.publish = publish  # the RecordingBus double, teed to a real bus
        rec = CallRecorder("c_e2e", tmp_path, clock, date="d", meta={"pair": "fake-fake"})
        await rec.start(real_bus)
        attach = {s: RecordingLeg(leg, rec, s) for s, leg in legs.items()}
    pcm_a = conversation(
        [("silence", 5600), ("speech", 1500), ("silence", 6400), ("speech", 800)]
        + [("silence", 4000)]
    )
    pcm_b = conversation(
        [("silence", 10600), ("speech", 1800), ("silence", 3400), ("speech", 400)]
        + [("silence", 2100)]
    )
    await session.attach_leg("a", attach["a"])
    await session.attach_leg("b", attach["b"])
    await drive_lockstep(session, legs, clock, {"a": pcm_a, "b": pcm_b})
    await session.wait_closed()
    if rec is not None:
        await rec.close()
    return session, legs, rec


async def test_e2e_call_is_identical_with_recording_on(tmp_path):
    plain, plain_legs, _ = await run_e2e(None)
    taped, taped_legs, rec = await run_e2e(tmp_path)
    assert [dataclasses.asdict(t) for t in taped.turn_log.turns] == [
        dataclasses.asdict(t) for t in plain.turn_log.turns
    ]
    for s in ("a", "b"):
        assert taped_legs[s].outbound == plain_legs[s].outbound
        assert taped_legs[s].log == plain_legs[s].log
    assert rec is not None
    d = rec.dir
    sizes = set()
    for name in ("a_in", "b_in", "a_out", "b_out"):
        with wave.open(str(d / f"{name}.wav"), "rb") as w:
            sizes.add(w.getnframes())
    assert len(sizes) == 1
    evs = lines(d / "events.jsonl")
    turns = [e for e in evs if e["kind"] == "turn"]
    assert len(turns) == len(taped.turn_log.turns)
    assert any(e["kind"] == "leg.mark_ack" for e in evs)
    assert [e["payload"]["status"] for e in evs if e["kind"] == "call"] == ["active", "ended"]
    summary = json.loads((d / "summary.json").read_text())
    assert summary["turns"] == 3 and summary["backchannels"] == 1
    # b_out holds what B was sent, where it played: the earcon starts at A1's play start
    a1 = taped.turn_log.turns[0]
    with wave.open(str(d / "b_out.wav"), "rb") as w:
        b_out = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    assert np.abs(b_out[16 * a1.t_play_start : 16 * (a1.t_play_start + 100)]).max() > 0
