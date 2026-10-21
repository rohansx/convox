"""FloorController: the doc's test-case table (floor-controller.md §4) and worked traces
(§6). Every step goes through the Rig, which asserts the invariants over the leg log."""

from __future__ import annotations

from dubhash.audio.frames import FRAME_BYTES
from tests.floor_helpers import FeedAudio, Rig, check_all_ended, frames_of, settle

# --- basic flow ------------------------------------------------------------------------


async def test_a_speaks_translation_plays_mark_returns_idle():
    rig = Rig()
    fc = rig.fc
    assert fc.state == "IDLE"
    await fc.on_speech_start("a")
    assert fc.state == "A_SPEAKING"
    rig.clock.advance(1500)
    await fc.on_speech_end("a")
    a1 = rig.tr("a", 1, n_frames=4)
    await rig.hand(a1)
    assert fc.state == "PLAYING_AB"
    assert fc.playing is a1
    b = rig.legs["b"]
    assert len(b.entries("send")) == 4 and all(
        len(e.data) == FRAME_BYTES for e in b.entries("send")
    )
    assert [e.tag for e in b.entries("mark")] == ["a:1"]
    assert rig.legs["a"].entries() == []
    rig.clock.advance(80)
    await rig.finish()
    assert fc.state == "IDLE" and fc.playing is None
    assert rig.played == [a1] and a1.clipped_ms is None
    assert a1.timings["t_play_start"] == 1500 and a1.timings["t_play_end"] == 1580
    check_all_ended(rig)
    states = [(e.payload["from"], e.payload["to"]) for e in rig.bus.kinds("state")]
    assert states == [("IDLE", "A_SPEAKING"), ("A_SPEAKING", "PLAYING_AB"), ("PLAYING_AB", "IDLE")]


async def test_translation_while_listener_holds_floor_is_queued():
    rig = Rig()
    fc = rig.fc
    await fc.on_speech_start("b")
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    assert fc.state == "B_SPEAKING" and list(fc.queue) == [a1]
    assert rig.legs["b"].entries() == []
    assert rig.bus.kinds("state")[-1].payload["queue_depth"] == 1


async def test_discarded_utterance_returns_idle_and_drains_queue():
    rig = Rig()
    fc = rig.fc
    await fc.on_speech_start("b")
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    await fc.on_speech_end("b")
    await fc.on_utterance_discarded("b")
    await rig.step()
    assert fc.state == "PLAYING_AB" and fc.playing is a1
    await rig.finish()
    assert fc.state == "IDLE"
    check_all_ended(rig)


async def test_discard_from_non_holder_changes_nothing():
    rig = Rig()
    fc = rig.fc
    await fc.on_speech_start("a")
    await fc.on_utterance_discarded("b")
    assert fc.state == "A_SPEAKING"


async def test_underrun_is_not_the_end_only_mark_is():
    rig = Rig()
    fc = rig.fc
    feed = FeedAudio()
    a1 = rig.tr("a", 1, audio=feed)
    feed.push(*frames_of(1, 2))
    await rig.hand(a1)
    assert fc.state == "PLAYING_AB"
    assert len(rig.legs["b"].entries("send")) == 2
    assert rig.legs["b"].entries("mark") == []
    await rig.advance(2000)  # TTS stalls for 2 s
    assert fc.state == "PLAYING_AB" and fc.playing is a1
    feed.push(*frames_of(1, 3))
    feed.end()
    await rig.step()
    assert len(rig.legs["b"].entries("send")) == 5
    assert [e.tag for e in rig.legs["b"].entries("mark")] == ["a:1"]
    assert fc.state == "PLAYING_AB"  # the mark has been requested, not fired
    await rig.finish()
    assert fc.state == "IDLE"


async def test_mark_with_wrong_tag_or_wrong_leg_is_ignored():
    rig = Rig()
    fc = rig.fc
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    await fc.on_mark("b", "a:99")
    await fc.on_mark("a", "a:1")  # the speaker's leg cannot end a→b playback
    await rig.step()
    assert fc.state == "PLAYING_AB" and fc.playing is a1


async def test_start_registers_leg_callbacks():
    rig = Rig()
    fc = rig.fc
    await fc.start()
    assert len(rig.legs["a"].mark_cbs) == 1 and len(rig.legs["b"].dtmf_cbs) == 1
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    await rig.finish()  # fires through the registered callback
    assert fc.state == "IDLE"
    rig.legs["a"].fire_dtmf("5")
    await settle()
    assert rig.bus.kinds("dtmf")[-1].payload == {"side": "a", "digit": "5"}
    await fc.aclose()


async def test_render_hook_shapes_listener_audio():
    rig = Rig()

    async def render(tr):
        yield b"\x00" * FRAME_BYTES  # an "earcon" frame
        async for c in tr.audio:
            yield c

    rig.fc = rig.make(render=render)
    a1 = rig.tr("a", 1, n_frames=2)
    await rig.hand(a1)
    sends = rig.legs["b"].entries("send")
    assert [e.data[0] for e in sends] == [0, 1, 1]


# --- overlap, backchannel, barge-in, order ---------------------------------------------


async def test_speaker_talks_900ms_during_playing_ba_no_clear_and_is_queued():
    rig = Rig()
    fc = rig.fc
    b1 = rig.tr("b", 1)
    await rig.hand(b1)
    assert fc.state == "PLAYING_BA"
    await fc.on_speech_start("a")
    await rig.advance(900)
    await fc.on_speech_end("a")
    await rig.advance(300)
    assert fc.state == "PLAYING_BA"
    assert rig.legs["a"].entries("clear") == [] and rig.bus.kinds("barge_in") == []
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    assert list(fc.queue) == [a1] and fc.state == "PLAYING_BA"
    await rig.finish()  # mark(a) for b1
    assert fc.state == "PLAYING_AB" and fc.playing is a1
    assert rig.sent_tags("b") == ["a:1"]
    await rig.finish()
    assert fc.state == "IDLE" and b1.clipped_ms is None and a1.clipped_ms is None
    check_all_ended(rig)


async def test_listener_backchannel_400ms_changes_nothing():
    rig = Rig()
    fc = rig.fc
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    n_events = len(rig.bus.events)
    await fc.on_speech_start("b")
    await rig.advance(400)
    await fc.on_speech_end("b")
    await rig.advance(2000)
    assert fc.state == "PLAYING_AB" and fc.playing is a1 and not fc.queue
    assert rig.legs["b"].entries("clear") == []
    assert len(rig.bus.events) == n_events  # no state change, no barge-in
    await rig.finish()
    assert a1.clipped_ms is None


async def test_listener_speaks_1500ms_barge_in_at_1200():
    rig = Rig()
    fc = rig.fc
    feed = FeedAudio()
    a1 = rig.tr("a", 1, audio=feed)
    feed.push(*frames_of(1, 15))  # 300 ms reach the leg before B cuts in
    await rig.hand(a1)
    await fc.on_speech_start("b")
    await rig.advance(1150)
    assert fc.state == "PLAYING_AB" and rig.legs["b"].entries("clear") == []
    await rig.advance(50)  # 1200 ms of continuous listener speech
    assert fc.state == "B_SPEAKING" and fc.playing is None
    assert [e.op for e in rig.legs["b"].entries() if e.op != "send"] == ["clear"]
    assert a1.clipped_ms == 300 and rig.played == [a1]
    assert a1.timings["t_play_end"] == 1200
    (ev,) = rig.bus.kinds("barge_in")
    assert ev.payload == {"side": "b", "listener": "b", "clipped_seq": 1, "clipped_ms": 300}
    feed.push(*frames_of(1, 5))
    await rig.advance(300)
    await fc.on_speech_end("b")
    assert len(rig.legs["b"].entries("send")) == 15  # nothing sent after the clear
    assert fc.state == "B_SPEAKING"
    check_all_ended(rig)


async def test_barge_in_from_speaker_side_is_ignored():
    rig = Rig()
    fc = rig.fc
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    await fc.on_speech_start("a")  # speaker keeps talking: no timer (§2.1 rule 3)
    await rig.advance(3000)
    await fc.on_barge_in("a")
    await rig.step()
    assert fc.state == "PLAYING_AB" and rig.legs["b"].entries("clear") == []


async def test_stale_mark_after_clear_is_ignored():
    rig = Rig()
    fc = rig.fc
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    await fc.on_speech_start("b")
    await rig.advance(1200)
    assert fc.state == "B_SPEAKING"
    b1 = rig.tr("b", 1)
    await rig.hand(b1)
    assert fc.playing is b1
    await fc.on_mark("b", "a:1")  # the transport reports the cleared item late
    await rig.step()
    assert fc.state == "PLAYING_BA" and fc.playing is b1
    assert rig.played == [a1]


async def test_order_a1_b1_a2_arrive_while_busy():
    rig = Rig()
    fc = rig.fc
    await fc.on_speech_start("b")  # B holds the floor, so a1 queues
    a1, b1, a2 = rig.tr("a", 1), rig.tr("b", 1), rig.tr("a", 2)
    await rig.hand(a1)
    await rig.hand(b1)  # src holds the floor, but a1 arrived first
    assert fc.playing is a1, "cross-direction order is arrival order"
    await rig.hand(a2)
    assert list(fc.queue) == [b1, a2]
    await rig.finish()
    assert fc.playing is b1
    await rig.finish()
    assert fc.playing is a2
    await rig.finish()
    assert [t.tag for t in rig.played] == ["a:1", "b:1", "a:2"]
    assert fc.state == "IDLE"
    check_all_ended(rig)


async def test_trace_6_2_real_barge_in():
    rig = Rig()
    fc = rig.fc
    feed = FeedAudio()
    a1 = rig.tr("a", 1, audio=feed)
    feed.push(*frames_of(1, 10))
    await rig.hand(a1)  # 1
    assert fc.state == "PLAYING_AB"
    await fc.on_speech_start("b")  # 2
    await rig.advance(1200)  # 3
    assert fc.state == "B_SPEAKING" and a1.clipped_ms == 200
    assert rig.legs["b"].entries("clear") and rig.bus.kinds("barge_in")
    await fc.on_speech_end("b")
    b1 = rig.tr("b", 1)
    await rig.hand(b1)  # 4
    assert fc.state == "PLAYING_BA" and rig.sent_tags("a") == ["b:1"]
    await rig.finish()  # 5
    assert fc.state == "IDLE"
    check_all_ended(rig)


async def test_trace_6_3_cross_direction_queueing_and_order():
    rig = Rig()
    fc = rig.fc
    a1, b1, a2 = rig.tr("a", 1), rig.tr("b", 1), rig.tr("a", 2)
    await rig.hand(a1)
    assert (fc.state, list(fc.queue)) == ("PLAYING_AB", [])
    await fc.on_speech_start("b")
    await rig.advance(800)
    await fc.on_speech_end("b")
    await rig.hand(b1)
    assert (fc.state, list(fc.queue)) == ("PLAYING_AB", [b1])
    await rig.hand(a2)
    assert (fc.state, list(fc.queue)) == ("PLAYING_AB", [b1, a2])
    await rig.finish()
    assert (fc.state, list(fc.queue)) == ("PLAYING_BA", [a2])
    await rig.finish()
    assert (fc.state, list(fc.queue)) == ("PLAYING_AB", [])
    await rig.finish()
    assert (fc.state, list(fc.queue)) == ("IDLE", [])
    assert rig.legs["a"].entries("clear") == [] and rig.legs["b"].entries("clear") == []
    check_all_ended(rig)
