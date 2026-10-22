"""CrosstalkGate on synthetic levels: cross-talk between co-located mics, and echo."""

from __future__ import annotations

import numpy as np
import pytest

from convox.session.crosstalk import CrosstalkGate, frame_dbfs


def frame_at(dbfs: float, f0: float = 140.0) -> bytes:
    """One 20 ms sine frame whose RMS is `dbfs`."""
    t = np.arange(320) / 16000
    amp = 32768 * 10 ** (dbfs / 20) * np.sqrt(2)
    return (np.sin(2 * np.pi * f0 * t) * amp).astype(np.int16).tobytes()


def feed(gate: CrosstalkGate, side, t0: int, ms: int, dbfs: float, voiced: bool = True):
    for t in range(t0, t0 + ms, 20):
        gate.on_frame(side, frame_at(dbfs), t, voiced)


def test_frame_dbfs():
    assert frame_dbfs(frame_at(-20.0)) == pytest.approx(-20.0, abs=0.2)
    assert frame_dbfs(b"\x00" * 640) <= -99


def test_quiet_copy_of_the_other_speaker_is_cross_talk():
    gate = CrosstalkGate(co_located=True)
    gate.utt_start("a", 0)
    feed(gate, "a", 0, 400, -20.0)
    db = gate.on_frame("b", frame_at(-32.0), 400, True)
    feed(gate, "b", 320, 80, -32.0)
    assert gate.frame_verdict("b", 400, db, in_utterance=False) == "crosstalk"


def test_a_reply_at_a_similar_level_is_not_cross_talk():
    gate = CrosstalkGate(co_located=True)
    gate.utt_start("a", 0)
    feed(gate, "a", 0, 420, -20.0)
    feed(gate, "b", 320, 80, -23.0)
    db = gate.on_frame("b", frame_at(-23.0), 400, True)
    assert gate.frame_verdict("b", 400, db, in_utterance=False) is None


def test_no_cross_talk_gate_on_separate_phone_lines():
    gate = CrosstalkGate(co_located=False)
    gate.utt_start("a", 0)
    feed(gate, "a", 0, 400, -20.0)
    db = gate.on_frame("b", frame_at(-32.0), 400, True)
    assert gate.frame_verdict("b", 400, db, in_utterance=False) is None
    assert gate.utterance_verdict("b", 0, 400) is None


def test_frame_gate_only_blocks_starts_not_an_open_utterance():
    gate = CrosstalkGate(co_located=True)
    gate.utt_start("a", 0)
    feed(gate, "a", 0, 400, -20.0)
    db = gate.on_frame("b", frame_at(-32.0), 400, True)
    assert gate.frame_verdict("b", 400, db, in_utterance=True) is None


def test_utterance_verdict_needs_the_other_side_first_and_louder():
    gate = CrosstalkGate(co_located=True)
    gate.utt_start("a", 100)
    feed(gate, "a", 100, 1500, -20.0)
    gate.utt_end("a", 1600)
    feed(gate, "b", 0, 1600, -32.0)  # b's VAD fired 100 ms early (jitter)
    o = gate.utterance_verdict("b", 0, 1600)
    assert o is not None and o.share > 0.9 and o.margin_db == pytest.approx(12, abs=0.5)
    assert gate.utterance_verdict("a", 100, 1600) is None  # the louder side is real


def test_similar_levels_are_two_real_speakers():
    gate = CrosstalkGate(co_located=True)
    gate.utt_start("a", 0)
    feed(gate, "a", 0, 1500, -20.0)
    feed(gate, "b", 400, 1100, -23.0)
    assert gate.utterance_verdict("b", 400, 1500) is None
    assert gate.provisional("b", 400, 600)


def test_started_well_before_the_other_side_is_not_cross_talk():
    gate = CrosstalkGate(co_located=True)
    feed(gate, "b", 0, 1600, -32.0)
    gate.utt_start("a", 800)
    feed(gate, "a", 800, 800, -20.0)
    assert gate.utterance_verdict("b", 0, 1600) is None


def test_speech_while_the_other_mic_is_quiet_clears_a_suspect_utterance():
    gate = CrosstalkGate(co_located=True)
    feed(gate, "a", 0, 300, -20.0)
    feed(gate, "b", 100, 200, -32.0)
    assert not gate.provisional("b", 100, 300)  # a quiet copy so far
    feed(gate, "a", 300, 200, -60.0, voiced=False)
    feed(gate, "b", 300, 200, -32.0)
    assert gate.provisional("b", 100, 500)  # a has stopped; b goes on alone


def test_forgotten_utterance_never_counts_as_first():
    gate = CrosstalkGate(co_located=True)
    gate.utt_start("a", 0)
    feed(gate, "a", 0, 1000, -20.0)
    gate.forget("a", 0)
    feed(gate, "b", 100, 900, -32.0)
    assert gate.utterance_verdict("b", 100, 1000) is None
    assert not gate.other_talking_first("b", 1000)


def test_echo_guard_while_playing_and_shortly_after():
    gate = CrosstalkGate(co_located=False)
    gate.playback_start("b")
    for _ in range(10):
        gate.playback_frame("b", frame_at(-20.0))
    gate.playback_frame("b", b"\x00" * 640)  # silence does not lower the reference
    quiet = gate.on_frame("b", frame_at(-35.0), 100, True)
    assert gate.frame_verdict("b", 100, quiet, in_utterance=False) == "echo"
    assert gate.frame_verdict("b", 100, quiet, in_utterance=True) == "echo"
    loud = gate.on_frame("b", frame_at(-18.0), 120, True)
    assert gate.frame_verdict("b", 120, loud, in_utterance=False) is None  # barge-in
    gate.playback_end("b", 1000)
    assert gate.frame_verdict("b", 1200, quiet, in_utterance=False) == "echo"  # tail
    assert gate.frame_verdict("b", 1400, quiet, in_utterance=False) is None
    assert gate.frame_verdict("a", 100, quiet, in_utterance=False) is None  # other leg
