from pathlib import Path

import pytest

from convox.config.pairs import load_pairs
from convox.config.settings import Settings
from convox.interp import vad as vad_mod
from convox.interp.mt_cache import CachedMT
from convox.providers.errors import ProviderUnavailable
from convox.providers.fakes import EnergyVAD, FakeMT, FakeSTT, FakeTTS
from convox.providers.sarvam_tts import BulbulTTS
from convox.providers.tts_cache import CachedTTS
from convox.session import providers as providers_mod
from convox.session.providers import Providers, build_providers

PAIRS = load_pairs(Path(__file__).resolve().parents[2] / "convox" / "config" / "pairs.yaml")


def settings(**kw) -> Settings:
    return Settings(_env_file=None, convox_env="dev", **kw)


def test_fake_pair_maps_to_fakes_per_side_and_cached_mt():
    p = build_providers(
        PAIRS["fake-fake"], settings(stt_impl="real", mt_impl="real", tts_impl="real")
    )
    assert isinstance(p, Providers)
    assert set(p.stt) == {"a", "b"} and set(p.tts) == {"a", "b"}
    assert all(isinstance(s, FakeSTT) for s in p.stt.values())
    assert all(isinstance(t, CachedTTS) and isinstance(t.inner, FakeTTS) for t in p.tts.values())
    assert p.stt["a"] is not p.stt["b"]  # one stream source per side
    assert isinstance(p.mt, CachedMT) and isinstance(p.mt.inner, FakeMT)


def test_real_pair_with_fake_impl_settings_gets_fakes():
    p = build_providers(PAIRS["hi-kn"], settings())
    assert isinstance(p.stt["a"], FakeSTT) and isinstance(p.tts["b"].inner, FakeTTS)
    assert isinstance(p.mt.inner, FakeMT)


def test_real_names_follow_the_pair_per_side(monkeypatch):
    built: dict[str, list[str]] = {"stt": [], "tts": [], "mt": []}

    def rec(kind):
        def build(name, s):
            built[kind].append(name)
            return {"stt": FakeSTT, "tts": FakeTTS, "mt": FakeMT}[kind]()

        return build

    monkeypatch.setattr(providers_mod, "build_stt", rec("stt"))
    monkeypatch.setattr(providers_mod, "build_tts", rec("tts"))
    monkeypatch.setattr(providers_mod, "build_mt", rec("mt"))
    build_providers(PAIRS["hi-kn"], settings(stt_impl="real", mt_impl="real", tts_impl="real"))
    assert built["stt"] == ["sarvam-translate", "sarvam"]  # hi side pivots via English
    assert built["tts"] == ["bulbul", "bulbul"]  # tts["a"] speaks hi, tts["b"] speaks kn
    assert built["mt"] == ["sarvam"]


def test_real_without_keys_raises_provider_unavailable():
    with pytest.raises(ProviderUnavailable):
        build_providers(PAIRS["hi-kn"], settings(stt_impl="real"))


def test_vad_factory_gives_a_fresh_vad_each_call():
    p = build_providers(PAIRS["fake-fake"], settings())
    v1, v2 = p.vad_factory(), p.vad_factory()
    assert v1 is not v2
    assert 0.0 <= v1.speech_prob(b"\x00" * 640) <= 1.0


def test_vad_falls_back_to_energy_when_silero_cannot_load(monkeypatch):
    def broken(*a, **k):
        raise vad_mod.VADModelError("no model")

    monkeypatch.setattr(providers_mod, "SileroVAD", broken)
    p = build_providers(PAIRS["fake-fake"], settings())
    assert isinstance(p.vad_factory().inner, EnergyVAD)  # behind the noise-floor gate


def test_every_tts_goes_through_the_audio_cache_and_fakes_get_their_own(tmp_path):
    s = settings(tts_cache_dir=tmp_path)
    p1 = build_providers(PAIRS["fake-fake"], s)
    p2 = build_providers(PAIRS["fake-fake"], s)
    # fakes: one store per call (tests never see another test's audio), no disk
    assert p1.tts["a"].store is p1.tts["b"].store
    assert p1.tts["a"].store is not p2.tts["a"].store
    assert p1.tts["a"].store.disk_dir is None
    assert p1.mt.shared is not p2.mt.shared


def test_real_tts_shares_one_process_store_with_disk(tmp_path):
    s = settings(sarvam_api_key="k", tts_impl="real", mt_impl="real", tts_cache_dir=tmp_path)
    p1 = build_providers(PAIRS["mr-hi"], s)
    p2 = build_providers(PAIRS["mr-hi"], s)
    assert isinstance(p1.tts["a"].inner, BulbulTTS)
    assert p1.tts["a"].inner is not p2.tts["a"].inner  # sockets stay per call
    assert p1.tts["a"].store is p2.tts["b"].store  # the audio is shared
    assert p1.tts["a"].store.disk_dir == tmp_path
    assert p1.mt.shared is p2.mt.shared  # phrase-pack translations too
    assert p1.prefill_rps == s.phrase_prefill_rps > 0


def test_tts_cache_dir_empty_means_memory_only():
    s = Settings(_env_file=None, convox_env="dev", tts_cache_dir="")
    assert s.tts_cache_dir is None
    assert settings().tts_cache_mb == 64
