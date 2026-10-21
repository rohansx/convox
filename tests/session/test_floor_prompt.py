"""FloorController.play_prompt: the opening line goes through the floor, is marked, and
holds interpretation playback to that leg until its mark comes back (or a timeout)."""

from __future__ import annotations

import asyncio

from dubhash.audio.frames import FRAME_BYTES
from tests.floor_helpers import Rig, frames_of, settle, tts

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


async def test_translation_to_a_leg_hearing_its_prompt_waits_for_the_mark():
    rig = Rig()
    fc = rig.fc
    await prompt(rig, "b")
    await fc.on_speech_start("a")
    rig.clock.advance(1500)
    await fc.on_speech_end("a")
    a1 = rig.tr("a", 1, n_frames=4)
    await rig.hand(a1)
    assert fc.playing is None and list(fc.queue) == [a1]  # queued, not dropped
    assert rig.sent_tags("b") == []
    await fc.on_mark("b", "opening")
    await rig.step()
    assert fc.playing is a1 and fc.state == "PLAYING_AB"
    assert not fc.prompt_playing("b")
    assert rig.sent_tags("b") == ["a:1"]
    await rig.finish()
    assert rig.played == [a1] and fc.state == "IDLE"


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
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    assert fc.playing is None
    await rig.advance(1100)  # under 200 + 1000
    assert fc.playing is None
    await rig.advance(200)
    assert fc.playing is a1 and not fc.prompt_playing("b")


async def test_a_stale_mark_after_the_timeout_does_nothing():
    rig = Rig()
    fc = rig.fc
    await prompt(rig, "b", n_frames=1, grace_ms=0)
    await rig.advance(100)
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    assert fc.playing is a1
    await fc.on_mark("b", "opening")
    await rig.step()
    assert fc.playing is a1  # not ended by the prompt's mark


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
    """Released while the listener holds the floor: it waits for the usual rules."""
    rig = Rig()
    fc = rig.fc
    await prompt(rig, "b")
    a1 = rig.tr("a", 1)
    await rig.hand(a1)
    await fc.on_speech_start("b")
    assert fc.state == "B_SPEAKING"
    await fc.on_mark("b", "opening")
    await rig.step()
    assert fc.playing is None and list(fc.queue) == [a1]
    await fc.on_utterance_discarded("b")
    await rig.step()
    assert fc.playing is a1
