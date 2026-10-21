"""FloorController.play_prompt: the opening line goes through the floor and is marked. A
translation for a leg still hearing its prompt does not wait for it (L7): the prompt is cut
(no more of it is sent, the leg is cleared) and the translation plays at once."""

from __future__ import annotations

import asyncio

from dubhash.audio.frames import FRAME_BYTES
from tests.floor_helpers import FeedAudio, Rig, frames_of, settle, tts

PROMPT_MARKER = 0xDD


async def prompt(rig: Rig, side, n_frames: int = 10, **kw) -> asyncio.Task[int]:
    task = asyncio.create_task(
        rig.fc.play_prompt(side, tts(frames_of(PROMPT_MARKER, n_frames)), **kw)
    )
    await settle()
    return task


async def test_prompt_is_sent_then_marked_on_its_leg():
    rig = Rig()
    task = await prompt(rig, "b")
    assert await task == 10 * FRAME_BYTES
    b = rig.legs["b"]
    assert [e.marker for e in b.entries("send")] == [PROMPT_MARKER] * 10
    assert [e.tag for e in b.entries("mark")] == ["opening"]
    assert rig.legs["a"].entries() == []
    assert rig.fc.prompt_playing("b") and not rig.fc.prompt_playing("a")


async def test_translation_to_a_leg_hearing_its_prompt_cuts_it():
    rig = Rig()
    fc = rig.fc
    feed = FeedAudio()
    task = asyncio.create_task(fc.play_prompt("b", feed))
    feed.push(*frames_of(PROMPT_MARKER, 3))
    await settle()
    await fc.on_speech_start("a")
    rig.clock.advance(1500)
    await fc.on_speech_end("a")
    a1 = rig.tr("a", 1, n_frames=4)
    await rig.hand(a1)
    assert fc.playing is a1 and fc.state == "PLAYING_AB"  # not queued behind the prompt
    assert not fc.prompt_playing("b")
    ops = [(e.op, e.marker or e.tag) for e in rig.legs["b"].entries()]
    m1 = next(k for k, t in rig.markers.items() if t is a1)
    first_a1 = ops.index(("send", m1))
    assert ("clear", "") in ops[:first_a1]  # what the phone still had queued is dropped
    assert ("mark", "opening") not in ops
    feed.push(*frames_of(PROMPT_MARKER, 5))  # the TTS keeps producing: none of it is sent
    feed.end()
    assert await task == 3 * FRAME_BYTES
    b_sends = [e.marker for e in rig.legs["b"].entries("send")]
    assert b_sends.count(PROMPT_MARKER) == 3
    await rig.finish()
    assert rig.played == [a1] and fc.state == "IDLE"
    assert any(e.kind == "prompt_cut" for e in rig.bus.events)


async def test_prompt_already_finished_sending_is_cut_too():
    """Sent and marked, but the phone is still playing it: clear it, play the translation."""
    rig = Rig()
    fc = rig.fc
    task = await prompt(rig, "b")
    await task
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    assert fc.playing is a1 and not fc.prompt_playing("b")
    ops = [e.op for e in rig.legs["b"].entries() if e.op != "send" or e.marker != PROMPT_MARKER]
    assert ops[:3] == ["mark", "clear", "send"]
    await fc.on_mark("b", "opening")  # the stale opening mark does not end a1
    await rig.step()
    assert fc.playing is a1


async def test_the_other_direction_is_not_held():
    rig = Rig()
    fc = rig.fc
    await prompt(rig, "b")
    b1 = rig.tr("b", 1, n_frames=3)
    await rig.hand(b1)  # plays to leg a, which is not hearing a prompt
    assert fc.playing is b1


async def test_hold_times_out_when_the_mark_never_comes():
    rig = Rig()
    fc = rig.fc
    await prompt(rig, "b", n_frames=10, grace_ms=1000)  # 200 ms of audio
    assert fc.prompt_playing("b")
    await rig.advance(1100)  # under 200 + 1000
    assert fc.prompt_playing("b")
    await rig.advance(200)
    assert not fc.prompt_playing("b")
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    assert fc.playing is a1 and [e.op for e in rig.legs["b"].entries("clear")] == []


async def test_failed_prompt_releases_the_hold():
    rig = Rig()
    fc = rig.fc

    async def broken():
        yield frames_of(PROMPT_MARKER, 1)[0]
        raise ConnectionError("tts down")

    a1 = rig.tr("a", 1)
    task = asyncio.create_task(fc.play_prompt("b", broken()))
    await settle()
    assert isinstance(task.exception(), ConnectionError)
    await rig.hand(a1)
    assert fc.playing is a1 and not fc.prompt_playing("b")


async def test_translation_queued_during_the_prompt_plays_when_the_floor_frees():
    """The listener holds the floor (talking over their own prompt): the translation waits
    for the usual rules, then cuts what is left of the prompt."""
    rig = Rig()
    fc = rig.fc
    await prompt(rig, "b")
    await fc.on_speech_start("b")
    assert fc.state == "B_SPEAKING"
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    assert fc.playing is None and list(fc.queue) == [a1]
    assert fc.prompt_playing("b")
    await fc.on_utterance_discarded("b")
    await rig.step()
    assert fc.playing is a1 and not fc.prompt_playing("b")
    assert [e.op for e in rig.legs["b"].entries("clear")] == ["clear"]
