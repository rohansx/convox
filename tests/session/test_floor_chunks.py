"""FloorController with chunked utterances (L9): the chunks of A's ongoing utterance play
to B while A is still speaking, in order, one behind the other; A keeps the floor between
them; the listener's barge-in still works; nothing is dropped. Every step goes through
the Rig, which checks the invariants (never two playing, never dropped, order per
direction) over the leg log."""

from __future__ import annotations

from dubhash.types import Side, Translation
from tests.floor_helpers import FeedAudio, Rig, check_all_ended, frames_of


def chunk(rig: Rig, side: Side, seq: int, n: int, *, final: bool, utt: int = 0, **kw):
    t: Translation = rig.tr(side, seq, **kw)
    t.utterance_id, t.chunk, t.final_chunk = utt, n, final
    return t


def states(rig: Rig) -> list[tuple[str, str]]:
    return [(e.payload["from"], e.payload["to"]) for e in rig.bus.kinds("state")]


async def test_chunk_plays_while_the_speaker_talks_and_the_floor_stays_theirs():
    rig = Rig()
    fc = rig.fc
    await fc.on_speech_start("a")
    a0 = chunk(rig, "a", 0, 0, final=False)
    await rig.hand(a0)  # A has not stopped: plays to B at once
    assert fc.state == "PLAYING_AB" and fc.playing is a0
    await rig.finish()
    assert fc.state == "A_SPEAKING"  # not IDLE: the rest of A's utterance is coming
    a1 = chunk(rig, "a", 1, 1, final=True)
    await rig.hand(a1)
    assert fc.playing is a1
    await fc.on_speech_end("a")
    await rig.finish()
    assert fc.state == "IDLE"
    assert rig.sent_tags("b") == ["a:0", "a:1"]
    check_all_ended(rig)
    assert states(rig) == [
        ("IDLE", "A_SPEAKING"),
        ("A_SPEAKING", "PLAYING_AB"),
        ("PLAYING_AB", "A_SPEAKING"),
        ("A_SPEAKING", "PLAYING_AB"),
        ("PLAYING_AB", "IDLE"),
    ]


async def test_next_chunk_queues_right_behind_the_playing_one():
    rig = Rig()
    fc = rig.fc
    await fc.on_speech_start("a")
    a0, a1, a2 = (chunk(rig, "a", i, i, final=i == 2) for i in range(3))
    await rig.hand(a0)
    await rig.hand(a1)
    await rig.hand(a2)
    assert fc.playing is a0 and list(fc.queue) == [a1, a2]
    await rig.finish()
    assert fc.playing is a1  # started in the mark's own handler: no IDLE between
    await rig.finish()
    await rig.finish()
    assert rig.sent_tags("b") == ["a:0", "a:1", "a:2"]
    assert ("PLAYING_AB", "IDLE") == states(rig)[-1]
    assert ("PLAYING_AB", "A_SPEAKING") not in states(rig)
    check_all_ended(rig)


async def test_the_other_side_waits_for_the_rest_of_the_chunked_utterance():
    """B talks (under 1.2 s) while A's chunk plays: B's translation arrives between A's
    chunks but plays after A's whole utterance, never in the middle of it."""
    rig = Rig()
    fc = rig.fc
    await fc.on_speech_start("a")
    a0 = chunk(rig, "a", 0, 0, final=False)
    await rig.hand(a0)
    await fc.on_speech_start("b")
    await rig.advance(500)
    await fc.on_speech_end("b")
    b1 = rig.tr("b", 0)
    await rig.hand(b1)
    assert list(fc.queue) == [b1]
    await rig.finish()  # a0 done; A is still mid-utterance: B's waits
    assert fc.state == "A_SPEAKING" and fc.playing is None and list(fc.queue) == [b1]
    a1 = chunk(rig, "a", 1, 1, final=True)
    await rig.hand(a1)
    assert fc.playing is a1 and list(fc.queue) == [b1]
    await rig.finish()
    assert fc.playing is b1 and fc.state == "PLAYING_BA"
    await rig.finish()
    assert fc.state == "IDLE"
    check_all_ended(rig)


async def test_listener_barge_in_cuts_a_chunk_and_takes_the_floor():
    rig = Rig()
    fc = rig.fc
    await fc.on_speech_start("a")
    feed = FeedAudio()
    feed.push(*frames_of(1, 10))
    a0 = chunk(rig, "a", 0, 0, final=False, audio=feed)
    await rig.hand(a0)
    await fc.on_speech_start("b")
    await rig.advance(1200)
    assert fc.state == "B_SPEAKING" and a0.clipped_ms == 200
    assert rig.legs["b"].entries("clear")
    a1 = chunk(rig, "a", 1, 1, final=True)
    await rig.hand(a1)  # B holds the floor now: A's rest waits
    assert fc.playing is None and list(fc.queue) == [a1]
    b1 = rig.tr("b", 0)
    await fc.on_speech_end("b")
    await rig.hand(b1)
    assert fc.playing is a1  # arrival order once B's turn has its translation
    await rig.finish()
    assert fc.playing is b1
    await rig.finish()
    check_all_ended(rig)


async def test_discarded_rest_of_a_chunked_utterance_frees_the_floor():
    rig = Rig()
    fc = rig.fc
    await fc.on_speech_start("a")
    await rig.hand(chunk(rig, "a", 0, 0, final=False))
    await fc.on_speech_start("b")
    await fc.on_speech_end("b")
    b1 = rig.tr("b", 0)
    await rig.hand(b1)
    await rig.finish()
    assert fc.state == "A_SPEAKING" and list(fc.queue) == [b1]
    await fc.on_utterance_discarded("a")  # the rest of A's utterance was empty
    await rig.step()
    assert fc.playing is b1
    await rig.finish()
    check_all_ended(rig)


async def test_held_floor_with_a_lost_final_chunk_is_rescued_after_4s():
    rig = Rig()
    fc = rig.fc
    await fc.on_speech_start("a")
    await rig.hand(chunk(rig, "a", 0, 0, final=False))
    await rig.finish()
    await fc.on_speech_end("a")
    b1 = rig.tr("b", 0)
    await rig.hand(b1)
    assert fc.playing is None
    await rig.advance(4100)
    assert fc.playing is b1
    await rig.finish()
    assert fc.state == "IDLE"
    check_all_ended(rig)


async def test_two_chunked_utterances_back_to_back_keep_order():
    rig = Rig()
    fc = rig.fc
    await fc.on_speech_start("a")
    items = [
        chunk(rig, "a", 0, 0, final=False, utt=0),
        chunk(rig, "a", 1, 1, final=True, utt=0),
        chunk(rig, "a", 2, 0, final=False, utt=1),
        chunk(rig, "a", 3, 1, final=True, utt=1),
    ]
    for t in items:
        await rig.hand(t)
    for _ in items:
        await rig.finish()
    assert rig.sent_tags("b") == ["a:0", "a:1", "a:2", "a:3"]
    assert fc.state == "IDLE"
    check_all_ended(rig)
