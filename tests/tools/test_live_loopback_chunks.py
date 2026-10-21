"""tools/live_loopback.py for chunked interpretation (L9): long scripts, what the listener
felt from the speaker's start, ear-voice span, pair overrides, waiting for whole
utterances."""

from __future__ import annotations

from dubhash.audio.earcon import EARCON
from dubhash.audio.frames import FRAME_BYTES
from dubhash.config.pairs import load_pairs
from tools import live_loopback as ll

LONG = {
    "long-hi-kn": ("hi-IN", "kn-IN"),
    "long-mr-hi": ("mr-IN", "hi-IN"),
    "long-mr-en": ("mr-IN", "en-IN"),
    "long-gu-en": ("gu-IN", "en-IN"),
}


def test_long_scripts_have_two_or_three_clauses_per_line():
    for key, langs in LONG.items():
        sc = ll.SCRIPTS[key]
        assert (sc.a_lang, sc.b_lang) == langs
        assert {ln.side for ln in sc.lines} == {"a", "b"}
        for ln in sc.lines:
            assert ln.text.count(",") >= 2, ln.text  # natural comma pauses
            assert len(ln.text.split()) >= 10, ln.text
        # a delivery or a clinic call with numbers to check
        assert any(any(ch.isdigit() for ch in ln.text) for ln in sc.lines)
    # the default script per language pair is unchanged
    assert ll.script_for("mr-IN", "hi-IN") == "delivery-mr-hi"


def test_measure_from_the_speakers_start():
    tone = b"\x00\x10" * (FRAME_BYTES // 2)
    rec = ll.SILENCE * 15 + EARCON + ll.SILENCE * 2 + tone * 4 + ll.SILENCE * 3
    row = ll.TurnRow(0, "a", "x", n_start=0, n_voice_end=10, n_next=len(rec) // FRAME_BYTES)
    row.n_voice_start = 2
    ll.measure(row, rec)
    assert row.heard_ms == 100 + 120 + 40
    assert row.from_start_ms == row.heard_ms + 8 * 20


def test_line_felt_calibrates_the_servers_chunks_to_the_ear():
    row = ll.TurnRow(0, "a", "x", n_start=0, n_voice_end=250, n_next=600)
    row.n_voice_start = 0
    row.from_start_ms = 4100  # heard 4.1 s after the line's first voiced frame
    row.server = [
        {"side": "a", "t_speech_start": 1000, "t_speech_end": 3000, "t_play_start": 5000,
         "backchannel": False, "utterance_id": 3, "chunk": 0},
        {"side": "a", "t_speech_start": 3400, "t_speech_end": 5000, "t_play_start": 6400,
         "backchannel": False, "utterance_id": 3, "chunk": 1},
    ]  # fmt: skip
    ll.line_felt(row)
    # server: first heard 4000 after its start; the ear adds 100
    assert row.chunks == 2
    srv_evs = round((4000 * 2000 + 3000 * 1600) / 3600)
    assert row.evs_ms == srv_evs + 100


def test_pairs_override_sets_every_pair(tmp_path):
    path = ll.pairs_override(tmp_path, chunking=True, chunk_pause_ms=250, chunk_min_ms=1000)
    pairs = load_pairs(path)
    assert pairs and all(
        (p.chunking, p.chunk_pause_ms, p.chunk_min_ms) == (True, 250, 1000) for p in pairs.values()
    )
    off = load_pairs(ll.pairs_override(tmp_path, chunking=False))
    assert not any(p.chunking for p in off.values())


def test_args_for_chunking():
    a = ll.parse_args(["--chunking", "--chunk-pause-ms", "400", "--chunk-min-ms", "1500"])
    assert (a.chunking, a.chunk_pause_ms, a.chunk_min_ms) == (True, 400, 1500)
    assert ll.parse_args([]).chunking is None


async def test_long_fake_lines_wait_for_the_whole_utterance(tmp_path):
    """Plumbing with fakes: a long line (clauses and pauses) is measured from its start,
    and the tool waits until the session is idle before the next line."""
    utts = ll.fake_utts(ll.SCRIPTS["long-mr-hi"].lines[:2], long=True)
    opts = ll.Opts(
        a_lang="mr-IN", b_lang="hi-IN", opening="skip", pause_ms=300, quiet_ms=400,
        turn_timeout_s=8,
    )  # fmt: skip
    with ll.serve({"dubhash_env": "test"}, None) as srv:
        result = await ll.run_call(srv, utts, opts)
    rows = result["rows"]
    assert [r.side for r in rows] == ["a", "b"]
    for r in rows:
        assert r.n_voice_end - r.n_voice_start > 150  # over 3 s of speech
        assert r.from_start_ms is not None and r.heard_ms is not None
        assert r.from_start_ms > r.heard_ms
    summary = ll.summarize(result)
    assert summary["from_start"]["n"] == 2
    assert "evs" in ll.table(result, summary)
