"""What the listener feels (L9): speech start -> first heard, ear-voice span, first audio.

An utterance is every turn of one (side, utterance_id); chunked interpretation (L9) splits
one utterance into several turns, unchunked it is one turn. Turns without utterance_id
(recordings from before L9) count as one utterance each.
"""

from __future__ import annotations

from typing import Any

from dubhash.store.timeline import analyze, utterance_metrics, utterances


def t(side: str, seq: int, start: int, end: int, play: int, **kw: Any) -> dict[str, Any]:
    d = {
        "side": side,
        "seq": seq,
        "t_speech_start": start,
        "t_speech_end": end,
        "t_eou": end + 550,
        "t_play_start": play,
        "t_play_end": None if play is None else play + 1000,
        "backchannel": False,
    }
    d.update(kw)
    return d


def test_unchunked_5s_sentence_first_heard_from_start_and_evs():
    # speaks 0..5000, heard 1400 ms after the end
    turns = [t("a", 0, 0, 5000, 6400, utterance_id=0, chunk=0, t_utt_start=0)]
    (u,) = utterances(turns)
    assert u["speech_start_to_first_heard"] == 6400
    assert u["ear_voice_span"] == 6400  # one part: said at 0, heard at 6400
    assert u["first_audio"] == 1400
    m = utterance_metrics(turns)
    assert m["speech_start_to_first_heard"] == {"n": 1, "p50": 6400, "p95": 6400}
    assert m["first_audio"]["p50"] == 1400


def test_chunks_of_one_utterance_group_and_weight_by_speech():
    turns = [
        # chunk 0 said 0..3000, heard at 4000; chunk 1 said 3400..5000, heard at 6400
        t("a", 0, 0, 3000, 4000, utterance_id=7, chunk=0, t_utt_start=0),
        t("a", 1, 3400, 5000, 6400, utterance_id=7, chunk=1, t_utt_start=0),
        t("b", 2, 8000, 9000, 10_500, utterance_id=0, chunk=0, t_utt_start=8000),
    ]
    us = utterances(turns)
    assert [(u["side"], u["chunks"]) for u in us] == [("a", 2), ("b", 1)]
    a = us[0]
    assert a["speech_start_to_first_heard"] == 4000
    # lags 4000 (3000 ms of speech) and 3000 (1600 ms): weighted mean
    assert a["ear_voice_span"] == round((4000 * 3000 + 3000 * 1600) / 4600)
    # first audio stays per utterance from its speech end: the last chunk's play start
    assert a["first_audio"] == 6400 - 5000
    m = utterance_metrics(turns)
    assert m["speech_start_to_first_heard"]["n"] == 2


def test_utterance_start_comes_from_t_utt_start_even_if_chunk_0_never_played():
    turns = [t("a", 3, 2000, 4000, 5000, utterance_id=1, chunk=1, t_utt_start=0)]
    (u,) = utterances(turns)
    assert u["speech_start_to_first_heard"] == 5000


def test_old_turns_without_utterance_id_are_one_utterance_each():
    turns = [t("a", 0, 0, 1000, 2400), t("a", 1, 5000, 6000, 7600)]
    assert [u["speech_start_to_first_heard"] for u in utterances(turns)] == [2400, 2600]


def test_backchannels_and_unplayed_turns_do_not_count():
    turns = [
        t("a", 0, 0, 300, None, backchannel=True),
        t("b", 1, 0, 1000, None, utterance_id=0),
    ]
    assert utterances(turns) == []


def test_a_later_chunk_waiting_behind_the_earlier_one_is_no_queue_warning():
    turns = [
        t("a", 0, 0, 3000, 4000, utterance_id=0, chunk=0, t_tts_ttfb=3900),
        t("a", 1, 3400, 5000, 6400, utterance_id=0, chunk=1, t_tts_ttfb=5200),
    ]
    out = analyze([{"kind": "turn", "ms": 0, "payload": {"turn": x}} for x in turns], [])
    assert [w["code"] for w in out["warnings"]] == []


def test_analyze_reports_the_three_metrics_server_and_heard():
    turn = t("a", 0, 1000, 6000, 7400, utterance_id=0, chunk=0, t_utt_start=1000)
    events = [
        {"kind": "leg.mark", "ms": 7500, "payload": {"side": "b", "tag": "a:0"}},
        {"kind": "turn", "ms": 8400, "payload": {"turn": turn}},
    ]
    client = [{"kind": "play_start", "side": "b", "tag": "a:0", "rx_ms": 7600}]
    out = analyze(events, client)
    assert out["speech_start_to_first_heard"]["p50"] == 6400
    assert out["ear_voice_span"]["p50"] == 6400
    assert out["first_audio"]["p50"] == 1400
    # on the phone's clock (no pong: its receive time)
    assert out["heard_from_start"]["p50"] == 6600
