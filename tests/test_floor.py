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
