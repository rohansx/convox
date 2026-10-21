import json
import subprocess
import sys
from pathlib import Path

from dubhash.audio.wav import read_wav, write_wav
from tests.audio_fixtures import conversation
from tools import bench

STAGES = {"eou", "stt_final", "mt", "tts_ttfb", "first_audio"}


def test_main_with_fakes_writes_wavs_and_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DUBHASH_ENV", "dev")
    rc = bench.main(["--out", str(tmp_path), "--call-id", "c_bench", "--vad", "energy"])
    assert rc == 0
    a, b = read_wav(tmp_path / "c_bench_a.wav"), read_wav(tmp_path / "c_bench_b.wav")
    assert a and b
    report = json.loads((tmp_path / "c_bench.json").read_text())
    assert report["call_id"] == "c_bench" and report["clock"] == "lockstep"
    assert report["source"] == "bench"
    assert set(report["stages"]) == STAGES
    for st in report["stages"].values():
        assert st["n"] == 3 and st["p50"] is not None and st["p95"] >= st["p50"]
    assert len(report["turns"]) == 3 and report["summary"]["turns"] == 3
    assert report["providers"]["mt"] == "fake"
    assert "first_audio" in capsys.readouterr().out


def test_main_reads_wav_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBHASH_ENV", "dev")
    wav_a, wav_b = tmp_path / "a.wav", tmp_path / "b.wav"
    write_wav(wav_a, conversation([("silence", 300), ("speech", 1200), ("silence", 3000)]))
    write_wav(wav_b, conversation([("silence", 4500)]))
    argv = ["--a-wav", str(wav_a), "--b-wav", str(wav_b), "--out", str(tmp_path / "out")]
    assert bench.main([*argv, "--call-id", "c_w", "--vad", "energy", "--no-opening"]) == 0
    report = json.loads((tmp_path / "out" / "c_w.json").read_text())
    assert [t["side"] for t in report["turns"]] == ["a"]
    assert report["audio_s"] == {"a": 4.5, "b": 4.5}


def test_help_runs_as_a_module():
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [sys.executable, "-m", "tools.bench", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for flag in ("--a-wav", "--b-wav", "--stt", "--mt", "--tts", "--out"):
        assert flag in out


LIVE_LIKE = ["--fake-mt-ms", "1200", "--fake-stt-final-ms", "290", "--fake-tts-ttfb-ms", "250"]
# hi-kn has early final on (L3), which holds speculation back; spec tests measure it alone
NO_EARLY = ["--early-final-ms", "0"]


def _spaced_wavs(tmp_path) -> list[str]:
    """Three A utterances far apart, B silent: no floor contention, so first_audio is the
    pipeline's own latency."""
    wav_a, wav_b = tmp_path / "spaced_a.wav", tmp_path / "spaced_b.wav"
    gap = ("silence", 6000)
    a = [("silence", 500), ("speech", 1500), gap, ("speech", 1200), gap, ("speech", 1400), gap]
    write_wav(wav_a, conversation(a))
    write_wav(wav_b, conversation([("silence", 500)]))
    return ["--a-wav", str(wav_a), "--b-wav", str(wav_b)]


def _bench(tmp_path, call_id: str, *extra: str) -> dict:
    argv = ["--out", str(tmp_path), "--call-id", call_id, "--vad", "energy", "--no-opening"]
    argv += _spaced_wavs(tmp_path)
    assert bench.main([*argv, *LIVE_LIKE, *extra]) == 0
    return json.loads((tmp_path / f"{call_id}.json").read_text())


def test_spec_flag_with_live_like_fake_delays(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DUBHASH_ENV", "dev")
    off = _bench(tmp_path, "c_off", "--no-spec", *NO_EARLY)
    on = _bench(tmp_path, "c_on", "--spec", *NO_EARLY)
    assert off["spec"]["enabled"] is False and off["spec"]["started"] == 0
    assert on["spec"]["enabled"] is True
    assert on["spec"]["eligible"] == 3 and on["spec"]["hits"] == 3 and on["spec"]["used"] == 3
    assert on["spec"]["hit_rate"] == 1.0
    assert off["stages"]["mt"]["p50"] >= 1200 and off["stages"]["stt_final"]["p50"] >= 290
    assert off["stages"]["tts_ttfb"]["p50"] >= 250
    fa_off = off["stages"]["first_audio"]["p50"]
    fa_on = on["stages"]["first_audio"]["p50"]
    assert fa_on <= fa_off - 400
    assert "spec" in capsys.readouterr().out


def test_spec_flag_shown_in_help():
    out = subprocess.run(
        [sys.executable, "-m", "tools.bench", "--help"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for flag in (
        "--spec",
        "--no-spec",
        "--spec-after-ms",
        "--fake-mt-ms",
        "--fake-stt-final-ms",
        "--fake-tts-ttfb-ms",
    ):
        assert flag in out


def test_spec_after_ms_overrides_pair(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBHASH_ENV", "dev")
    late = _bench(tmp_path, "c_late", "--spec", "--spec-after-ms", "200", *NO_EARLY)
    early = _bench(tmp_path, "c_early", "--spec", "--spec-after-ms", "40", *NO_EARLY)
    assert late["spec"]["after_ms"] == 200 and early["spec"]["after_ms"] == 40
    fa = early["stages"]["first_audio"]["p50"]
    assert fa <= late["stages"]["first_audio"]["p50"] - 140


def test_early_final_ms_flag_cuts_first_audio(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DUBHASH_ENV", "dev")
    off = _bench(tmp_path, "c_ef0", "--no-spec", "--early-final-ms", "0")
    on = _bench(tmp_path, "c_ef200", "--no-spec", "--early-final-ms", "200")
    assert off["early_final"] == {
        "early_final_ms": 0,
        "started": 0,
        "restarts": 0,
        "used": 0,
        "restart_rate": None,
    }
    assert on["early_final"]["early_final_ms"] == 200
    assert on["early_final"]["started"] == 3 and on["early_final"]["used"] == 3
    assert on["early_final"]["restarts"] == 0 and on["early_final"]["restart_rate"] == 0.0
    # 550 ms EOU, then final 290 + MT 1200 + TTS 250 after an end() sent 350 ms earlier
    fa_off = off["stages"]["first_audio"]["p50"]
    assert on["stages"]["first_audio"]["p50"] <= fa_off - 300
    assert [t["src_text"] for t in on["turns"]] == [t["src_text"] for t in off["turns"]]
    assert "early_final" in capsys.readouterr().out


def test_report_has_what_the_listener_felt(tmp_path, monkeypatch, capsys):
    """L9: per-utterance speech start -> first heard, ear-voice span, first audio."""
    monkeypatch.setenv("DUBHASH_ENV", "dev")
    assert bench.main(["--out", str(tmp_path), "--call-id", "c_felt", "--vad", "energy"]) == 0
    report = json.loads((tmp_path / "c_felt.json").read_text())
    felt = report["felt"]
    assert set(felt) == {"speech_start_to_first_heard", "ear_voice_span", "first_audio"}
    assert felt["speech_start_to_first_heard"]["n"] == 3
    # unchunked: heard after the whole utterance, so from its start > from its end
    assert felt["speech_start_to_first_heard"]["p50"] > felt["first_audio"]["p50"]
    assert "speech_start_to_first_heard" in capsys.readouterr().out


def test_long_synthetic_utterances_have_comma_pauses(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBHASH_ENV", "dev")
    inbound = bench.synthetic_inbound(long=True)
    assert len(inbound["a"]) > len(bench.synthetic_inbound()["a"])
    argv = ["--out", str(tmp_path), "--call-id", "c_long", "--vad", "energy", "--long"]
    assert bench.main(argv) == 0
    report = json.loads((tmp_path / "c_long.json").read_text())
    assert report["felt"]["speech_start_to_first_heard"]["n"] == 3
