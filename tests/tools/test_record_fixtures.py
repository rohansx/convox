import csv
import wave
from pathlib import Path

import numpy as np
import pytest

from tools import record_fixtures as rf


def _tone(ms: int = 500, rate: int = 8000, amp: int = 6000) -> bytes:
    t = np.arange(rate * ms // 1000) / rate
    return (np.sin(2 * np.pi * 200 * t) * amp).astype(np.int16).tobytes()


def _manifest_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --- prompts --------------------------------------------------------------------------


def test_load_txt_prompts_numbers_ids(tmp_path: Path):
    p = tmp_path / "hi.txt"
    p.write_text("# comment\nगेट पर आ जाओ\n\nपाँच मिनट\n", encoding="utf-8")
    prompts = rf.load_prompts(p, lang="hi")
    assert [(x.id, x.lang, x.text) for x in prompts] == [
        ("hi_001", "hi", "गेट पर आ जाओ"),
        ("hi_002", "hi", "पाँच मिनट"),
    ]


def test_load_txt_needs_lang(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_text("hello\n")
    with pytest.raises(ValueError, match="--lang"):
        rf.load_prompts(p, lang=None)


def test_load_csv_prompts(tmp_path: Path):
    p = tmp_path / "p.csv"
    p.write_text("id,lang,text\nkn_a,kn,ಸರಿ\nkn_b,kn,ಹೌದು\n", encoding="utf-8")
    prompts = rf.load_prompts(p, lang=None)
    assert [x.id for x in prompts] == ["kn_a", "kn_b"]
    assert prompts[0].text == "ಸರಿ"


def test_load_csv_lang_override_filters(tmp_path: Path):
    p = tmp_path / "p.csv"
    p.write_text("id,lang,reference_text\na,hi,x\nb,kn,y\n", encoding="utf-8")
    assert [x.id for x in rf.load_prompts(p, lang="kn")] == ["b"]


def test_duplicate_ids_rejected(tmp_path: Path):
    p = tmp_path / "p.csv"
    p.write_text("id,lang,text\na,hi,x\na,hi,y\n")
    with pytest.raises(ValueError, match="duplicate"):
        rf.load_prompts(p, lang=None)


# --- session --------------------------------------------------------------------------


def test_session_writes_wavs_and_manifest(tmp_path: Path):
    prompts = [rf.Prompt("hi_001", "hi", "गेट"), rf.Prompt("hi_002", "hi", "मिनट")]
    answers = iter(["", "", "", ""])  # start, keep, start, keep
    n = rf.run_session(
        prompts, tmp_path, rate=8000, ask=lambda _: next(answers), record=lambda: _tone(),
        say=lambda *_: None,
    )  # fmt: skip
    assert n == 2
    rows = _manifest_rows(tmp_path / "manifest.csv")
    assert [r["id"] for r in rows] == ["hi_001", "hi_002"]
    assert rows[0]["reference_text"] == "गेट"
    assert rows[0]["path"] == "hi/hi_001.wav"
    assert list(rows[0]) == ["id", "lang", "reference_text", "path"]
    with wave.open(str(tmp_path / "hi" / "hi_001.wav")) as w:
        assert w.getframerate() == 8000 and w.getnchannels() == 1 and w.getsampwidth() == 2


def test_session_skip_redo_and_quit(tmp_path: Path):
    prompts = [rf.Prompt(f"hi_00{i}", "hi", f"t{i}") for i in range(1, 4)]
    # p1: skip. p2: start, redo, start, keep. p3: quit.
    answers = iter(["s", "", "r", "", "", "q"])
    takes = []

    def record() -> bytes:
        takes.append(1)
        return _tone()

    n = rf.run_session(prompts, tmp_path, 8000, ask=lambda _: next(answers), record=record,
                       say=lambda *_: None)  # fmt: skip
    assert n == 1
    assert len(takes) == 2
    assert [r["id"] for r in _manifest_rows(tmp_path / "manifest.csv")] == ["hi_002"]


def test_session_resumes_and_skips_recorded(tmp_path: Path):
    prompts = [rf.Prompt("hi_001", "hi", "a"), rf.Prompt("hi_002", "hi", "b")]
    rf.run_session(prompts[:1], tmp_path, 8000, ask=lambda _: "", record=_tone,
                   say=lambda *_: None)  # fmt: skip
    seen = []
    rf.run_session(
        prompts, tmp_path, 8000, ask=lambda q: seen.append(q) or "", record=_tone,
        say=lambda *_: None,
    )  # fmt: skip
    rows = _manifest_rows(tmp_path / "manifest.csv")
    assert [r["id"] for r in rows] == ["hi_001", "hi_002"]


def test_level_warnings():
    assert rf.level_warning(b"") == "empty recording"
    assert "quiet" in rf.level_warning(_tone(amp=100))
    assert "clipp" in rf.level_warning(_tone(amp=32767))
    assert rf.level_warning(_tone(amp=6000)) is None


# --- cli ------------------------------------------------------------------------------


def test_main_without_sounddevice_prints_instructions(tmp_path: Path, monkeypatch, capsys):
    p = tmp_path / "hi.txt"
    p.write_text("गेट पर आ जाओ\n", encoding="utf-8")
    monkeypatch.setattr(rf, "_import_sounddevice", lambda: None)
    code = rf.main([str(p), "--lang", "hi", "--out", str(tmp_path / "corpus")])
    out = capsys.readouterr().out
    assert code == 2
    assert "sounddevice" in out
    assert "rec -r 8000 -c 1 -b 16" in out
    assert "hi_001.wav" in out
    assert not (tmp_path / "corpus" / "manifest.csv").exists()


def test_main_manifest_only_without_sounddevice(tmp_path: Path, monkeypatch):
    p = tmp_path / "hi.txt"
    p.write_text("a\nb\n", encoding="utf-8")
    monkeypatch.setattr(rf, "_import_sounddevice", lambda: None)
    out = tmp_path / "corpus"
    assert rf.main([str(p), "--lang", "hi", "--out", str(out), "--manifest-only"]) == 0
    assert [r["path"] for r in _manifest_rows(out / "manifest.csv")] == [
        "hi/hi_001.wav",
        "hi/hi_002.wav",
    ]
