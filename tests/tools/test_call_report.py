"""tools/call_report.py over a fixture call directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools import call_report

META = {
    "pair": "mr-hi",
    "mode": "interpret",
    "sides": {"a": {"lang": "mr-IN", "stt_lang": "mr-IN"}, "b": {"lang": "hi-IN"}},
}


def turn(side: str, seq: int, start: int, **kw: Any) -> dict[str, Any]:
    t = {
        "side": side, "seq": seq, "t_speech_start": start, "t_speech_end": start + 1500,
        "t_eou": start + 2050, "t_stt_final": start + 2200, "t_mt": start + 2400,
        "t_tts_ttfb": start + 2700, "t_play_start": start + 2750, "t_play_end": start + 4750,
        "src_text": None, "tgt_text": None, "backchannel": False, "clipped_ms": None,
    }  # fmt: skip
    t.update(kw)
    return t


def write_call(base: Path, call_id: str = "c_fix") -> Path:
    d = base / "2026-10-21" / call_id
    d.mkdir(parents=True)
    turns = [
        turn("a", 0, 1000),  # clean
        turn("b", 1, 1200, t_speech_end=2400),  # cross-talk: B's phone heard A
        turn("a", 2, 10_000, t_play_start=13_500, t_play_end=15_000),  # queue wait 800
        turn("b", 3, 20_000, t_speech_end=29_000, t_eou=29_550, t_stt_final=29_700,
             t_mt=29_900, t_tts_ttfb=30_200, t_play_start=30_250, t_play_end=31_000),  # long
        turn("a", 4, 40_000, t_stt_final=41_000),  # early-final restart
        turn("a", 5, 50_000, src_text="मला औषध हवे आहे", tgt_text="मला औषध हवे आहे"),  # lang
        turn("b", 6, 60_000, t_play_start=63_400, t_play_end=65_000),  # opening hold
        turn("a", 7, 63_500),  # echo: made while b:6 played to a
    ]  # fmt: skip
    events: list[dict[str, Any]] = [{"ts": "t", "ms": 0, "kind": "rec.meta", "payload": META}]
    events.append(
        {"ts": "t", "ms": 59_000, "kind": "leg.mark", "payload": {"side": "a", "tag": "opening"}}
    )  # noqa: E501
    events.append(
        {
            "ts": "t",
            "ms": 63_390,
            "kind": "leg.mark_ack",
            "payload": {"side": "a", "tag": "opening"},
        }
    )  # noqa: E501
    for t in turns:
        events.append({"ts": "t", "ms": t["t_play_end"], "kind": "turn", "payload": {"turn": t}})
    events.append(
        {
            "ts": "t",
            "ms": 70_000,
            "kind": "call",
            "payload": {"status": "ended", "reason": "hangup"},
        }
    )  # noqa: E501
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    client = [
        {"side": "b", "rx_ms": 1040, "kind": "pong", "server_ms": 1000, "client_ms": 11_020},
        {"side": "b", "rx_ms": 4300, "kind": "play_start", "client_ms": 14_000},
    ]
    (d / "client.jsonl").write_text("".join(json.dumps(c) + "\n" for c in client))
    return d


def test_prints_timeline_warnings_and_where_the_time_went(tmp_path, capsys):
    d = write_call(tmp_path)
    assert call_report.main([str(d)]) == 0
    out = capsys.readouterr().out
    assert "c_fix" in out and "mr-hi" in out
    assert "TIMELINE" in out and "WARNINGS" in out and "WHERE THE TIME WENT" in out
    for code in ("cross_talk", "queue_wait", "long_utterance", "early_final_restart",
                 "wrong_language", "opening_hold"):  # fmt: skip
        assert code in out, code
    assert "800!" in out  # the large queue gap is highlighted
    assert "4.00" in out  # A0's client play start, corrected by the ping offset


def test_finds_a_call_by_id_under_the_recordings_dir(tmp_path, capsys):
    write_call(tmp_path, "c_byid")
    assert call_report.main(["c_byid", "--dir", str(tmp_path)]) == 0
    assert "c_byid" in capsys.readouterr().out


def test_json_output(tmp_path, capsys):
    d = write_call(tmp_path)
    assert call_report.main([str(d), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["call_id"] == "c_fix" and len(doc["turns"]) == 8
    codes = {w["code"] for w in doc["warnings"]}
    assert {"cross_talk", "queue_wait", "long_utterance", "early_final_restart"} <= codes
    assert {"wrong_language", "opening_hold", "echo"} <= codes
    assert doc["stages"]["eou_wait"] is not None


def test_lang_decisions_get_their_own_section(tmp_path, capsys):
    d = write_call(tmp_path)
    lines = (d / "events.jsonl").read_text()
    lang = {"side": "a", "lang": "ta-IN", "state": "CONFIRMED", "confidence": 0.95,
            "source": "lid"}  # fmt: skip
    menu = {"side": "b", "lang": None, "state": "UNKNOWN", "confidence": 0.0, "source": "lid",
            "menu": True}  # fmt: skip
    extra = [
        {"ts": "t", "ms": 2500, "kind": "lang", "payload": lang},
        {"ts": "t", "ms": 3000, "kind": "lang", "payload": menu},
    ]
    (d / "events.jsonl").write_text(lines + "".join(json.dumps(e) + "\n" for e in extra))
    assert call_report.main([str(d)]) == 0
    out = capsys.readouterr().out
    assert "LANGUAGE (2)" in out
    assert "2.50  a  ta-IN" in out and "CONFIRMED" in out and "lid 0.95" in out
    assert "keypad menu" in out


def test_no_lang_section_without_auto_sides(tmp_path, capsys):
    assert call_report.main([str(write_call(tmp_path))]) == 0
    assert "LANGUAGE" not in capsys.readouterr().out


def test_unknown_call_is_an_error(tmp_path, capsys):
    with pytest.raises(SystemExit):
        call_report.main(["c_missing", "--dir", str(tmp_path)])
