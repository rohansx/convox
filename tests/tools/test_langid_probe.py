import numpy as np

from dubhash.interp.langid import SUPPORTED_LANGS
from tools.langid_probe import (
    CORPUS,
    VOICES,
    confusion,
    join_pairs,
    pstn,
    summarise,
    sweep,
    trim_lead,
    vote_rows,
    window,
)


def test_corpus_has_ten_sentences_per_supported_language_and_two_voices():
    assert sorted(CORPUS) == sorted(SUPPORTED_LANGS)
    assert all(len(v) == 10 for v in CORPUS.values())
    assert set(VOICES) == {"male", "female"}


def test_pstn_round_trip_keeps_length_and_rate_but_loses_band():
    t = np.arange(16000) / 16000
    hi = (8000 * np.sin(2 * np.pi * 6000 * t)).astype(np.int16).tobytes()  # above 4 kHz
    lo = (8000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16).tobytes()
    out_hi, out_lo = pstn(hi), pstn(lo)
    assert abs(len(out_hi) - len(hi)) <= 64 and abs(len(out_lo) - len(lo)) <= 64
    rms = lambda b: float(np.sqrt(np.mean(np.frombuffer(b, np.int16).astype(float) ** 2)))  # noqa: E731
    assert rms(out_hi) < 0.05 * rms(hi)  # 6 kHz is gone at 8 kHz sampling
    assert rms(out_lo) > 0.8 * rms(lo)


def test_window_clips_to_ms_or_keeps_all():
    pcm = b"\x00\x00" * 48000
    assert len(window(pcm, 1000)) == 32000
    assert window(pcm, None) == pcm


def test_trim_lead_keeps_100ms_before_the_first_loud_frame():
    silence = b"\x00\x00" * 8000  # 500 ms
    loud = (np.ones(16000) * 5000).astype(np.int16).tobytes()
    out = trim_lead(silence + loud)
    assert len(out) == 3200 + len(loud)


def test_join_pairs_makes_long_utterances_from_consecutive_sentences():
    a, b, c = b"\x01\x00" * 10, b"\x02\x00" * 10, b"\x03\x00" * 10
    clips = {("hi-IN", 0, "male"): a, ("hi-IN", 1, "male"): b, ("hi-IN", 2, "male"): c}
    out = join_pairs(clips, gap_ms=10)
    assert out == {("hi-IN", 100, "male"): a + b"\x00" * 320 + b}


def test_vote_rows_apply_the_engine_decision_to_two_models():
    def row(model, raw, conf, idx=0):
        return {"engine": "rest", "model": model, "lang": "mr-IN", "idx": idx, "voice": "male",
                "cond": "pstn", "window_ms": 2000, "raw": raw, "conf": conf, "ms": 200}  # fmt: skip

    rows = [
        row("saaras:v3", "mr-IN", 0.4), row("saaras:v4", "mr-IN", 1.0),
        row("saaras:v3", "mr-IN", 0.4, 1), row("saaras:v4", "hi-IN", 1.0, 1),
    ]  # fmt: skip
    out = vote_rows(rows, "saaras:v3", "saaras:v4")
    assert [(r["engine"], r["raw"], r["idx"]) for r in out] == [
        ("vote", "mr-IN", 0),
        ("vote", None, 1),
    ]


def test_replay_merges_saved_runs_and_adds_the_vote(tmp_path, monkeypatch):
    import json

    import tools.langid_probe as probe

    monkeypatch.setattr(probe, "OUT", tmp_path)

    def row(model, raw, conf):
        return {"engine": "rest", "model": model, "lang": "hi-IN", "idx": 0, "voice": "male",
                "cond": "pstn", "window_ms": 2000, "raw": raw, "conf": conf, "ms": 150}  # fmt: skip

    (tmp_path / "a.json").write_text(json.dumps({"rows": [row("saaras:v3", "hi-IN", 0.5)]}))
    (tmp_path / "b.json").write_text(json.dumps({"rows": [row("saaras:v4", "hi-IN", 1.0)]}))
    paths = [str(tmp_path / "a.json"), str(tmp_path / "b.json")]
    assert probe.replay(paths, ["saaras:v3", "saaras:v4"], "all") == 0
    out = json.loads((tmp_path / "results-all.json").read_text())
    vote = out["analysis"]["vote|saaras:v3+saaras:v4|short|pstn|2.0s"]
    assert vote["n"] == 1 and vote["accuracy"] == 1.0
    assert (tmp_path / "summary-all.md").exists()


def test_confusion_counts_truth_by_prediction_with_none_as_reject():
    rows = [("hi-IN", "hi-IN"), ("mr-IN", "hi-IN"), ("mr-IN", None), ("mr-IN", "mr-IN")]
    m = confusion(rows)
    assert m["hi-IN"]["hi-IN"] == 1
    assert m["mr-IN"]["hi-IN"] == 1 and m["mr-IN"]["none"] == 1 and m["mr-IN"]["mr-IN"] == 1


def test_sweep_reports_coverage_and_error_among_accepted():
    # (truth, raw lang, confidence)
    dets = [("hi-IN", "hi-IN", 0.99), ("mr-IN", "hi-IN", 0.7), ("mr-IN", "mr-IN", 0.9)]
    s = {r["threshold"]: r for r in sweep(dets, SUPPORTED_LANGS, thresholds=(0.5, 0.8))}
    assert s[0.5]["accepted"] == 3 and s[0.5]["wrong"] == 1
    assert s[0.8]["accepted"] == 2 and s[0.8]["wrong"] == 0
    assert s[0.8]["coverage"] == 2 / 3


def test_sweep_never_accepts_a_language_outside_the_candidates():
    s = sweep([("hi-IN", "ne-IN", 0.99)], SUPPORTED_LANGS, thresholds=(0.5,))
    assert s[0]["accepted"] == 0


def test_summarise_gives_accuracy_and_pair_confusions():
    dets = [
        ("hi-IN", "hi-IN", 0.99),
        ("mr-IN", "hi-IN", 0.9),
        ("gu-IN", "gu-IN", 0.9),
        ("hi-IN", "gu-IN", 0.9),
    ]
    s = summarise(dets, [100, 200, 300, 400])
    assert s["n"] == 4 and s["accuracy"] == 0.5
    assert s["mr_as_hi"] == 1 and s["hi_as_mr"] == 0
    assert s["hi_as_gu"] == 1 and s["gu_as_hi"] == 0
    assert s["latency_ms"]["p50"] == 200
