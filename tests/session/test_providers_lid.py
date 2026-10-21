"""build_providers(..., lid=True) adds a LanguageID for calls with lang "auto" sides."""

import sys
import types
from pathlib import Path

from dubhash.config.pairs import load_pairs
from dubhash.config.settings import Settings
from dubhash.providers.fakes import FakeLID
from dubhash.session.providers import build_lid, build_providers

PAIRS = load_pairs(Path(__file__).resolve().parents[2] / "dubhash" / "config" / "pairs.yaml")


def settings(**kw) -> Settings:
    return Settings(_env_file=None, dubhash_env="dev", **kw)


def test_no_lid_unless_asked():
    assert build_providers(PAIRS["hi-kn"], settings()).lid is None


def test_fake_impl_gives_the_fake_lid():
    assert isinstance(build_providers(PAIRS["hi-kn"], settings(), lid=True).lid, FakeLID)
    test_env = Settings(_env_file=None, dubhash_env="test", stt_impl="real")
    assert isinstance(build_lid(test_env), FakeLID)


def test_real_impl_uses_the_sarvam_engine_when_it_is_there(monkeypatch):
    class SarvamLID:
        name = "sarvam"

        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

    mod = types.ModuleType("dubhash.providers.sarvam_lid")
    mod.SarvamLID = SarvamLID  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dubhash.providers.sarvam_lid", mod)
    lid = build_lid(settings(stt_impl="real", sarvam_api_key="k"))
    assert isinstance(lid, SarvamLID) and lid.api_key == "k"


def test_real_impl_without_the_engine_has_no_lid(monkeypatch):
    monkeypatch.setitem(sys.modules, "dubhash.providers.sarvam_lid", None)  # ImportError
    assert build_lid(settings(stt_impl="real", sarvam_api_key="k")) is None
