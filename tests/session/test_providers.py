from pathlib import Path

import pytest

from dubhash.config.pairs import load_pairs
from dubhash.config.settings import Settings
from dubhash.interp import vad as vad_mod
from dubhash.interp.mt_cache import CachedMT
from dubhash.providers.errors import ProviderUnavailable
from dubhash.providers.fakes import EnergyVAD, FakeMT, FakeSTT, FakeTTS
from dubhash.session import providers as providers_mod
from dubhash.session.providers import Providers, build_providers

PAIRS = load_pairs(Path(__file__).resolve().parents[2] / "dubhash" / "config" / "pairs.yaml")


def settings(**kw) -> Settings:
    return Settings(_env_file=None, dubhash_env="dev", **kw)


def test_fake_pair_maps_to_fakes_per_side_and_cached_mt():
    p = build_providers(
        PAIRS["fake-fake"], settings(stt_impl="real", mt_impl="real", tts_impl="real")
    )
    assert isinstance(p, Providers)
    assert set(p.stt) == {"a", "b"} and set(p.tts) == {"a", "b"}
    assert all(isinstance(s, FakeSTT) for s in p.stt.values())
    assert all(isinstance(t, FakeTTS) for t in p.tts.values())
    assert p.stt["a"] is not p.stt["b"]  # one stream source per side
    assert isinstance(p.mt, CachedMT) and isinstance(p.mt.inner, FakeMT)


def test_real_pair_with_fake_impl_settings_gets_fakes():
    p = build_providers(PAIRS["hi-kn"], settings())
    assert isinstance(p.stt["a"], FakeSTT) and isinstance(p.tts["b"], FakeTTS)
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
    assert built["stt"] == ["sarvam", "sarvam"]
    assert built["tts"] == ["eleven", "bulbul"]  # tts["a"] speaks hi, tts["b"] speaks kn
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
    assert isinstance(p.vad_factory(), EnergyVAD)
