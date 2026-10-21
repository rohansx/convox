import asyncio
import time

import pytest

from dubhash.audio.frames import FRAME_BYTES
from dubhash.clock import FakeClock
from dubhash.legs.base import LegTransport
from dubhash.legs.fake import FakeLeg


def test_fake_leg_satisfies_protocol():
    assert isinstance(FakeLeg("a"), LegTransport)


async def test_frames_yields_640_byte_frames_until_end_input():
    leg = FakeLeg("a", b"\x01" * 1000)
    leg.feed(b"\x02" * 600)
    leg.end_input()
    frames = [f async for f in leg.frames()]
    assert [len(f) for f in frames] == [FRAME_BYTES] * 3
    assert frames[0] == b"\x01" * FRAME_BYTES
    assert frames[2].endswith(b"\x00")


async def test_feed_while_running_and_close_ends_frames():
    leg = FakeLeg("b")
    got: list[bytes] = []

    async def consume():
        async for f in leg.frames():
            got.append(f)

    task = asyncio.create_task(consume())
    leg.feed(b"\x00" * FRAME_BYTES * 2)
    await asyncio.sleep(0)
    await leg.close()
    await asyncio.wait_for(task, 1)
    assert len(got) == 2
    assert ("close", ()) in leg.log


async def test_close_is_idempotent():
    leg = FakeLeg("a")
    await leg.close()
    await leg.close()
    assert [k for k, _ in leg.log].count("close") == 1


async def test_advance_clock_moves_fake_clock_20ms_per_frame():
    clock = FakeClock()
    leg = FakeLeg("a", b"\x00" * FRAME_BYTES * 5, clock=clock, advance_clock=True)
    leg.end_input()
    stamps = [clock.now_ms() async for _ in leg.frames()]
    assert stamps == [20, 40, 60, 80, 100]


async def test_on_frame_hook_called_per_frame():
    seen: list[int] = []
    leg = FakeLeg("a", b"\x00" * FRAME_BYTES * 3, on_frame=seen.append)
    leg.end_input()
    _ = [f async for f in leg.frames()]
    assert seen == [0, 1, 2]


async def test_realtime_paces_at_20ms():
    leg = FakeLeg("a", b"\x00" * FRAME_BYTES * 3, realtime=True)
    leg.end_input()
    t0 = time.monotonic()
    n = len([f async for f in leg.frames()])
    assert n == 3
    assert time.monotonic() - t0 >= 0.035


async def test_send_captures_outbound():
    leg = FakeLeg("a", playout=False)
    await leg.send(b"\x01" * 640)
    await leg.send(b"\x02" * 1280)
    assert leg.outbound == b"\x01" * 640 + b"\x02" * 1280
    assert ("send", (640,)) in leg.log


async def test_mark_without_playout_fires_on_next_loop_iteration():
    leg = FakeLeg("a", playout=False)
    fired: list[str] = []
    leg.on_mark(fired.append)
    await leg.send(b"\x00" * 32000)
    await leg.mark("a:1")
    assert fired == []
    await asyncio.sleep(0)
    assert fired == ["a:1"]
    assert ("mark_fired", ("a:1",)) in leg.log


async def test_mark_with_playout_waits_for_simulated_playback_on_fake_clock():
    clock = FakeClock()
    leg = FakeLeg("b", clock=clock)
    fired: list[str] = []
    leg.on_mark(fired.append)
    await leg.send(b"\x00" * 16000)  # 500 ms
    await leg.mark("b:1")
    clock.advance(499)
    leg.poll()
    await asyncio.sleep(0)
    assert fired == []
    clock.advance(1)
    leg.poll()
    assert fired == ["b:1"]


async def test_mark_fires_while_frames_advance_the_clock():
    clock = FakeClock()
    leg = FakeLeg("a", b"\x00" * FRAME_BYTES * 10, clock=clock, advance_clock=True)
    leg.end_input()
    fired: list[tuple[str, int]] = []
    leg.on_mark(lambda tag: fired.append((tag, clock.now_ms())))
    await leg.send(b"\x00" * 3200)  # 100 ms
    await leg.mark("m")
    _ = [f async for f in leg.frames()]
    assert fired == [("m", 100)]


async def test_mark_with_playout_real_time():
    leg = FakeLeg("a")
    fired: list[str] = []
    leg.on_mark(fired.append)
    await leg.send(b"\x00" * 640)  # 20 ms
    await leg.mark("rt")
    await asyncio.sleep(0.06)
    assert fired == ["rt"]
    await leg.close()


async def test_clear_drops_unplayed_audio_and_pending_marks():
    clock = FakeClock()
    leg = FakeLeg("a", clock=clock)
    fired: list[str] = []
    leg.on_mark(fired.append)
    await leg.send(b"\x01" * 32000)  # 1 s
    await leg.mark("x")
    clock.advance(250)
    await leg.clear()
    assert len(leg.outbound) == 8000  # 250 ms played
    assert ("clear", (24000,)) in leg.log
    clock.advance(5000)
    leg.poll()
    await asyncio.sleep(0)
    assert fired == []


async def test_inject_dtmf_fires_callbacks():
    leg = FakeLeg("a")
    digits: list[str] = []
    leg.on_dtmf(digits.append)
    leg.inject_dtmf("1")
    leg.inject_dtmf("0")
    assert digits == ["1", "0"]
    assert ("dtmf", ("1",)) in leg.log


def test_advance_clock_requires_fake_clock():
    with pytest.raises(ValueError):
        FakeLeg("a", advance_clock=True)
