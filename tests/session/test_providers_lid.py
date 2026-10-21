"""build_providers(..., lid=True) adds a LanguageID for calls with lang "auto" sides."""

from pathlib import Path

from dubhash.config.pairs import load_pairs
from dubhash.config.settings import Settings
from dubhash.providers.fakes import FakeLID
from dubhash.providers.sarvam_lid import SarvamLID
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


def test_real_impl_uses_the_sarvam_engine():
    assert isinstance(build_lid(settings(stt_impl="real", sarvam_api_key="k")), SarvamLID)


def test_real_impl_without_a_key_has_no_lid():
    """No key: no LID (the call still resolves from STT finals, else the keypad menu)."""
    assert build_lid(settings(stt_impl="real")) is None
