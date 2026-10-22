import asyncio
import wave
from pathlib import Path

import pytest

from convox.providers.errors import ProviderError
from convox.providers.fakes import FakeMT, FakeSTT, FakeTTS
from tools import smoke

ALL_KEYS = smoke.Keys(sarvam="k1", eleven="k2", deepgram="k3")


def _fakes(**over) -> smoke.Factories:
    base = dict(
        mt=lambda key: FakeMT(),
        bulbul=lambda key: FakeTTS(ms_per_char=20),
        eleven=lambda key: FakeTTS(ms_per_char=20),
        sarvam_stt=lambda key: FakeSTT(),
        deepgram_stt=lambda key: FakeSTT(),
    )
    base.update(over)
    return smoke.Factories(**base)


def _wavs(d: Path, pattern: str) -> list[str]:
    return sorted(p.name for p in d.glob(pattern))


def _opts(tmp_path: Path, *extra: str):
    return smoke.parse_args(["--fast", "--out", str(tmp_path), *extra])


# --- argument parsing -----------------------------------------------------------------


def test_parse_defaults():
    a = smoke.parse_args([])
    assert a.only == {"mt", "tts", "stt"}
    assert a.out == Path("out/smoke")
    assert a.env_file == Path(".env")
    assert a.fast is False
    assert a.eou is False
    assert a.idle == []
    assert a.timeout > 0


def test_parse_only_and_idle():
    a = smoke.parse_args(["--only", "mt,tts", "--idle", "5,30,70", "--eou", "--fast"])
    assert a.only == {"mt", "tts"}
    assert a.idle == [5, 30, 70]
    assert a.eou and a.fast


@pytest.mark.parametrize("bad", [["--only", "mt,nope"], ["--idle", "5,x"], ["--idle", "-1"]])
def test_parse_rejects_bad_values(bad):
    with pytest.raises(SystemExit):
        smoke.parse_args(bad)


# --- summary formatting and exit codes ------------------------------------------------


def test_format_summary_table():
    rs = [
        smoke.Result("sarvam", "mt hi→kn #1", "ok", latency_ms=212, detail="ನಮಸ್ಕಾರ"),
        smoke.Result("eleven", "tts hi #1", "ok", latency_ms=900, ttfb_ms=310),
        smoke.Result("deepgram", "stt", "skip", detail="DEEPGRAM_API_KEY not set"),
        smoke.Result("sarvam", "stt kn #1", "fail", detail="ProviderError: boom"),
    ]
    text = smoke.format_summary(rs)
    lines = text.splitlines()
    assert lines[0].split()[:6] == ["vendor", "check", "status", "ttfb", "total", "detail"]
    assert any("212 ms" in ln and "mt hi→kn #1" in ln for ln in lines)
    assert any("310 ms" in ln and "900 ms" in ln for ln in lines)
    assert "FAIL" in text and "skip" in text
    assert lines[-1] == "2 ok, 1 failed, 1 skipped"


def test_format_summary_truncates_long_detail():
    text = smoke.format_summary([smoke.Result("sarvam", "x", "ok", detail="a" * 500)])
    assert max(len(ln) for ln in text.splitlines()) < 160


def test_exit_codes():
    ok = smoke.Result("s", "c", "ok")
    fail = smoke.Result("s", "c", "fail")
    skip = smoke.Result("s", "c", "skip")
    info = smoke.Result("s", "c", "info")
    assert smoke.exit_code([ok, skip, info]) == 0
    assert smoke.exit_code([ok, fail]) == 1
    assert smoke.exit_code([skip, skip]) == 2
    assert smoke.exit_code([]) == 2


# --- orchestration with fakes ---------------------------------------------------------


async def test_full_run_with_fakes(tmp_path: Path):
    rs = await smoke.run_smoke(ALL_KEYS, _opts(tmp_path), _fakes())
    by = {(r.vendor, r.check): r for r in rs}
    mt = [r for r in rs if r.check.startswith("mt ")]
    assert len(mt) == 3 and all(r.status == "ok" for r in mt)
    assert all(r.latency_ms is not None for r in mt)
    assert "[kn-IN]" in mt[0].detail
    tts = [r for r in rs if r.check.startswith("tts ")]
    assert {r.vendor for r in tts} == {"bulbul", "eleven"}
    assert all(r.status == "ok" and r.ttfb_ms is not None for r in tts)
    wavs = _wavs(tmp_path, "*.wav")
    assert wavs == ["bulbul_kn_1.wav", "bulbul_kn_2.wav", "eleven_hi_1.wav", "eleven_hi_2.wav"]
    with wave.open(str(tmp_path / "bulbul_kn_1.wav")) as w:
        assert w.getframerate() == 16000 and w.getnframes() > 0
    stt = [r for r in rs if r.check.startswith("stt ")]
    assert {r.vendor for r in stt} == {"sarvam", "deepgram"}
    assert all(r.status == "ok" for r in stt)
    assert len([r for r in stt if r.vendor == "sarvam"]) == 4
    assert len([r for r in stt if r.vendor == "deepgram"]) == 2  # hi wavs only
    assert by[("sarvam", "stt kn #1")].detail.startswith("w1")
    assert smoke.exit_code(rs) == 0


async def test_bulbul_uses_mt_output(tmp_path: Path):
    tts = FakeTTS(ms_per_char=5)
    await smoke.run_smoke(
        ALL_KEYS, _opts(tmp_path, "--only", "mt,tts"), _fakes(bulbul=lambda k: tts)
    )
    assert tts.requests[0][0].startswith("[kn-IN] ")
    assert tts.requests[0][1:] == ("bulbul:kn-female-1", "kn-IN")


async def test_missing_keys_are_skipped_with_clear_message(tmp_path: Path):
    keys = smoke.Keys(sarvam="k1", eleven="", deepgram="")
    rs = await smoke.run_smoke(keys, _opts(tmp_path), _fakes())
    skips = {r.vendor: r.detail for r in rs if r.status == "skip"}
    assert "ELEVENLABS_API_KEY" in skips["eleven"]
    assert "DEEPGRAM_API_KEY" in skips["deepgram"]
    assert "expected" in skips["deepgram"]
    assert not _wavs(tmp_path, "eleven_*.wav")
    assert smoke.exit_code(rs) == 0


async def test_no_keys_runs_nothing(tmp_path: Path):
    rs = await smoke.run_smoke(smoke.Keys("", "", ""), _opts(tmp_path), _fakes())
    assert rs and all(r.status == "skip" for r in rs)
    assert smoke.exit_code(rs) == 2


class _BrokenMT:
    name = "broken"

    async def translate(self, text: str, src: str, tgt: str) -> str:
        raise ProviderError("sarvam translate: HTTP 500")


async def test_failure_is_reported_and_others_still_run(tmp_path: Path):
    tts = FakeTTS(ms_per_char=5)
    rs = await smoke.run_smoke(
        ALL_KEYS, _opts(tmp_path), _fakes(mt=lambda k: _BrokenMT(), bulbul=lambda k: tts)
    )
    mt = [r for r in rs if r.check.startswith("mt ")]
    assert all(r.status == "fail" and "HTTP 500" in r.detail for r in mt)
    assert tts.requests[0][0] == smoke.KN_FALLBACK[0]  # falls back to fixed kn text
    assert smoke.exit_code(rs) == 1


async def test_timeout_is_a_failure(tmp_path: Path):
    slow = FakeMT(delay_ms=500)
    rs = await smoke.run_smoke(
        ALL_KEYS, _opts(tmp_path, "--only", "mt", "--timeout", "0.05"), _fakes(mt=lambda k: slow)
    )
    assert all(r.status == "fail" and "timeout" in r.detail for r in rs if r.vendor == "sarvam")


async def test_stt_without_tts_audio_is_skipped(tmp_path: Path):
    rs = await smoke.run_smoke(ALL_KEYS, _opts(tmp_path, "--only", "stt"), _fakes())
    stt = [r for r in rs if r.vendor == "sarvam"]
    assert stt and all(r.status == "skip" for r in stt)
    assert "no TTS audio" in stt[0].detail


async def test_stt_reads_existing_wavs_from_out_dir(tmp_path: Path):
    await smoke.run_smoke(ALL_KEYS, _opts(tmp_path, "--only", "tts"), _fakes())
    rs = await smoke.run_smoke(ALL_KEYS, _opts(tmp_path, "--only", "stt"), _fakes())
    assert [r.status for r in rs if r.vendor == "sarvam"] == ["ok"] * 4


def test_main_with_fakes_writes_json(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(smoke, "load_keys", lambda env_file: ALL_KEYS)
    monkeypatch.setattr(smoke, "real_factories", _fakes)
    out = tmp_path / "res.json"
    code = smoke.main(["--fast", "--out", str(tmp_path), "--json", str(out), "--only", "mt"])
    assert code == 0
    assert '"status": "ok"' in out.read_text()
    assert "vendor" in capsys.readouterr().out


# --- opt-in experiments (Q2, Q4) with scripted fakes ----------------------------------


class _EouStream:
    def __init__(self, fire_after_silent: int | None) -> None:
        self.eou_signal = asyncio.Event()
        self._silent = 0
        self._fire = fire_after_silent

    async def push(self, frame: bytes) -> None:
        if not any(frame):
            self._silent += 1
            if self._fire is not None and self._silent >= self._fire:
                self.eou_signal.set()

    async def end(self) -> str:
        return "ನಮಸ್ಕಾರ"

    async def aclose(self) -> None:
        pass


class _Sock:
    def __init__(self, dead: bool) -> None:
        self.dead = asyncio.Event()
        if dead:
            self.dead.set()


class _ScriptedSTT:
    name = "sarvam"

    def __init__(self, fire_after_silent=None, socket_dead=False) -> None:
        self.fire = fire_after_silent
        self._idle = {"kn-IN": [_Sock(socket_dead)]}

    async def open(self, lang: str):
        return _EouStream(self.fire)


async def test_eou_experiment_reports_signal(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(smoke, "FRAME_MS", 1)  # speed up real-time pacing in the test
    await smoke.run_smoke(ALL_KEYS, _opts(tmp_path, "--only", "tts"), _fakes())
    made = []

    def factory(key, **kw):
        made.append(kw)
        return _ScriptedSTT(fire_after_silent=3)

    rs = await smoke.run_smoke(
        ALL_KEYS, _opts(tmp_path, "--only", "mt", "--eou"), _fakes(sarvam_stt=factory)
    )
    q2 = next(r for r in rs if r.check == "q2 vad eou")
    assert q2.status == "info" and "speech_end after" in q2.detail
    assert made == [{"endpointing": "vad"}]


async def test_eou_experiment_without_signal(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(smoke, "FRAME_MS", 1)
    monkeypatch.setattr(smoke, "EOU_WAIT_MS", 50)
    await smoke.run_smoke(ALL_KEYS, _opts(tmp_path, "--only", "tts"), _fakes())
    rs = await smoke.run_smoke(
        ALL_KEYS,
        _opts(tmp_path, "--only", "mt", "--eou"),
        _fakes(sarvam_stt=lambda key, **kw: _ScriptedSTT()),
    )
    assert "no vad.speech_end" in next(r for r in rs if r.check == "q2 vad eou").detail


async def test_idle_experiment_reports_socket_state(tmp_path: Path):
    await smoke.run_smoke(ALL_KEYS, _opts(tmp_path, "--only", "tts"), _fakes())
    rs = await smoke.run_smoke(
        ALL_KEYS,
        _opts(tmp_path, "--only", "mt", "--idle", "0"),
        _fakes(sarvam_stt=lambda key, **kw: _ScriptedSTT(socket_dead=kw["keepalive_s"] > 100)),
    )
    q4 = {r.check: r.detail for r in rs if r.check.startswith("q4")}
    assert q4["q4 idle 0s no ping"].startswith("socket closed by server")
    assert q4["q4 idle 0s ping"].startswith("socket alive")
    assert smoke.exit_code(rs) == 0


def test_load_keys_reads_env_file(tmp_path: Path, monkeypatch):
    for var in ("SARVAM_API_KEY", "ELEVENLABS_API_KEY", "DEEPGRAM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text("SARVAM_API_KEY=abc\nELEVENLABS_API_KEY=\n")
    keys = smoke.load_keys(env)
    assert keys == smoke.Keys(sarvam="abc", eleven="", deepgram="")
