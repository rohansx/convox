"""Timeline analysis of a recorded call: per-turn rows, warnings, where the time went."""

from __future__ import annotations

from typing import Any

from dubhash.store.timeline import analyze, clock_offset, script_of

META = {
    "kind": "rec.meta",
    "ms": 0,
    "payload": {
        "pair": "mr-hi",
        "mode": "interpret",
        "sides": {
            "a": {"lang": "mr-IN", "stt_lang": "mr-IN"},
            "b": {"lang": "hi-IN", "stt_lang": "hi-IN"},
        },
    },
}


def turn(side: str, seq: int, start: int, **kw: Any) -> dict[str, Any]:
    t = {
        "side": side,
        "seq": seq,
        "t_speech_start": start,
        "t_speech_end": start + 1500,
        "t_eou": start + 2050,
        "t_stt_final": start + 2200,
        "t_mt": start + 2400,
        "t_tts_ttfb": start + 2700,
        "t_play_start": start + 2750,
        "t_play_end": start + 4750,
        "src_text": None,
        "tgt_text": None,
        "backchannel": False,
        "clipped_ms": None,
    }
    t.update(kw)
    return {"kind": "turn", "ms": t["t_play_end"] or start, "payload": {"turn": t}}


def ev(kind: str, ms: int, **payload: Any) -> dict[str, Any]:
    return {"kind": kind, "ms": ms, "payload": payload}


def codes(result: dict[str, Any]) -> list[tuple[str, str, int | None]]:
    return [(w["code"], w.get("side"), w.get("seq")) for w in result["warnings"]]


def test_turn_row_has_every_stage_and_the_gaps_sum_to_first_audio():
    events = [META, ev("leg.mark", 3800, side="b", tag="a:0"), turn("a", 0, 1000)]
    events.append(ev("leg.mark_ack", 5760, side="b", tag="a:0"))
    out = analyze(events, [])
    (row,) = out["turns"]
    assert row["side"] == "a" and row["listener"] == "b"
    assert row["speech_start"] == 1000 and row["play_start"] == 3750
    assert row["mark_ack"] == 5760
    gaps = row["gaps"]
    assert gaps == {
        "eou_wait": 550,
        "stt": 150,
        "mt": 200,
        "tts": 300,
        "queue": 50,
        "client": None,
    }
    assert row["first_audio_ms"] == 1250
    assert out["first_audio"] == {"n": 1, "p50": 1250, "p95": 1250}
    assert out["warnings"] == []


def test_client_play_start_is_corrected_by_the_clock_offset():
    # page clock = server clock + 10_000; rtt 40 ms on the best ping
    client = [
        {"side": "b", "rx_ms": 1040, "type": "telemetry", "kind": "pong", "server_ms": 1000,
         "client_ms": 11_020},
        {"side": "b", "rx_ms": 2200, "type": "telemetry", "kind": "pong", "server_ms": 2000,
         "client_ms": 12_100},  # rtt 200: worse, ignored
        {"side": "b", "rx_ms": 4300, "type": "telemetry", "kind": "play_start",
         "client_ms": 14_000},
    ]  # fmt: skip
    assert clock_offset(client, "b") == {"offset_ms": 10_000, "rtt_ms": 40, "n": 2}
    out = analyze([META, turn("a", 0, 1000)], client)
    row = out["turns"][0]
    assert row["client_play_start"] == 4000
    assert row["gaps"]["client"] == 250
    assert row["heard_ms"] == 1500
    assert out["stages"]["client"] == 250


def test_client_play_start_matches_by_tag_when_the_page_sends_one():
    client = [
        {"side": "b", "rx_ms": 3790, "kind": "play_start", "client_ms": 3790, "tag": "x:9"},
        {"side": "b", "rx_ms": 3990, "kind": "play_start", "client_ms": 3990, "tag": "a:0"},
    ]
    events = [META, ev("leg.mark", 3800, side="b", tag="a:0"), turn("a", 0, 1000)]
    row = analyze(events, client)["turns"][0]
    assert row["client_play_start"] == 3990


def test_flags_cross_talk_when_the_listener_mic_makes_a_turn_while_the_speaker_talks():
    events = [META, turn("a", 0, 1000), turn("b", 1, 1200, t_speech_end=2400)]
    out = analyze(events, [])
    assert ("cross_talk", "b", 1) in codes(out)


def test_flags_echo_when_a_turn_is_made_of_the_translation_playing_to_that_side():
    events = [META, turn("a", 0, 1000), turn("b", 1, 3900)]  # a:0 plays to b 3750..5750
    assert ("echo", "b", 1) in codes(analyze(events, []))


def test_flags_queue_wait_over_500_ms():
    events = [META, turn("a", 0, 1000, t_play_start=4400, t_play_end=6400)]
    out = analyze(events, [])
    (w,) = out["warnings"]
    assert w["code"] == "queue_wait" and w["ms_value"] == 700


def test_flags_long_utterance_over_8_s():
    events = [META, turn("a", 0, 1000, t_speech_end=9500, t_eou=10_050, t_stt_final=10_200,
                         t_mt=10_400, t_tts_ttfb=10_700, t_play_start=10_750,
                         t_play_end=12_000)]  # fmt: skip
    assert ("long_utterance", "a", 0) in codes(analyze(events, []))


def test_flags_early_final_restarts():
    events = [META, turn("a", 0, 1000, early_final_restarts=2)]
    assert ("early_final_restart", "a", 0) in codes(analyze(events, []))
    events = [META, turn("a", 0, 1000, t_stt_final=2300)]  # final before speech ended
    assert ("early_final_restart", "a", 0) in codes(analyze(events, []))


def test_flags_wrong_language_text():
    # Marathi speaker (a), translation for the Hindi listener came out in Marathi
    events = [META, turn("a", 0, 1000, src_text="मला औषध हवे आहे", tgt_text="मला औषध हवे आहे")]
    out = analyze(events, [])
    assert ("wrong_language", "a", 0) in codes(out)
    ok = [META, turn("a", 0, 1000, src_text="मला औषध हवे आहे", tgt_text="मुझे दवा चाहिए है")]
    assert analyze(ok, [])["warnings"] == []
    latin = [META, turn("b", 0, 1000, src_text="I need the medicine")]
    assert ("wrong_language", "b", 0) in codes(analyze(latin, []))


def test_flags_opening_hold_delaying_a_turn():
    events = [
        META,
        ev("leg.mark", 900, side="b", tag="opening"),
        ev("leg.mark_ack", 4700, side="b", tag="opening"),
        turn("a", 0, 1000, t_play_start=4720, t_play_end=6720),
    ]
    out = analyze(events, [])
    assert codes(out) == [("opening_hold", "a", 0)]
    assert out["warnings"][0]["ms_value"] == 1020


def test_backchannels_are_listed_but_not_counted_in_latency():
    bc = turn("b", 1, 6000, t_mt=None, t_tts_ttfb=None, t_play_start=None, t_play_end=None,
              backchannel=True)  # fmt: skip
    out = analyze([META, turn("a", 0, 1000), bc], [])
    assert [r["backchannel"] for r in out["turns"]] == [False, True]
    assert out["first_audio"]["n"] == 1


def test_stage_means_over_turns():
    events = [META, turn("a", 0, 1000), turn("b", 1, 10_000, t_tts_ttfb=12_900,
                                                t_play_start=12_950)]  # fmt: skip
    st = analyze(events, [])["stages"]
    assert st["tts"] == 400 and st["eou_wait"] == 550 and st["first_audio"] == 1350


def test_script_of():
    assert script_of("मुझे दवा") == "deva"
    assert script_of("મને દવા") == "gujr"
    assert script_of("hello there") == "latn"
    assert script_of("123 ...") is None
