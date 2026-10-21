"""Floor hooks for lang "auto": a DTMF hook that can take a digit before the floor's own
1/0 meanings (the keypad language menu), and bypass entered by the session itself (same
language on both sides)."""

from __future__ import annotations

import asyncio

from tests.floor_helpers import Rig


async def test_a_digit_the_hook_takes_never_reaches_replay_or_escape():
    rig = Rig()
    taken: list[tuple[str, str]] = []

    def hook(side, digit):
        taken.append((side, digit))
        return True

    fc = rig.make(dtmf_hook=hook)
    await fc.on_dtmf("a", "0")
    await fc.on_dtmf("a", "1")
    assert taken == [("a", "0"), ("a", "1")]
    assert not fc.bypass and rig.bypassed == 0
    # still published, so the console and recorder see the key press
    assert [e.payload["digit"] for e in rig.bus.kinds("dtmf")] == ["0", "1"]


async def test_a_digit_the_hook_declines_keeps_its_old_meaning():
    rig = Rig()
    fc = rig.make(dtmf_hook=lambda side, digit: False)
    await fc.on_dtmf("b", "0")
    assert fc.bypass and rig.bypassed == 1


async def test_enter_bypass_from_the_session_publishes_its_reason():
    rig = Rig()
    fc = rig.fc
    await fc.enter_bypass("a", reason="same_lang")
    assert fc.bypass and rig.bypassed == 1
    assert rig.bus.kinds("bypass")[-1].payload == {"side": "a", "reason": "same_lang"}
    await fc.enter_bypass("a", reason="same_lang")  # idempotent
    assert rig.bypassed == 1


async def test_an_uncuttable_prompt_holds_translations_until_its_mark():
    """The keypad menu must be heard whole: a translation for that leg waits for it."""
    from tests.floor_helpers import frames_of, settle, tts

    rig = Rig()
    fc = rig.fc
    await fc.start()
    task = asyncio.create_task(
        fc.play_prompt("a", tts(frames_of(0xEC, 5)), tag="menu", cuttable=False)
    )
    await settle()
    await rig.hand(rig.tr("b", 0))  # B's translation, for A
    assert fc.playing is None and fc.prompt_playing("a")
    assert rig.bus.kinds("prompt_cut") == []
    await task
    rig.legs["a"].fire_mark("menu")
    await settle()
    assert fc.playing is not None or rig.played  # started once the menu was heard


async def test_enter_bypass_ends_queued_translations_as_clipped():
    rig = Rig()
    fc = rig.fc
    await rig.hand(rig.tr("a", 0, n_frames=50))
    await rig.hand(rig.tr("a", 1))
    await fc.enter_bypass("b", reason="same_lang")
    assert fc.state == "IDLE"
    assert [t.clipped_ms is not None for t in rig.played] == [True, True]
