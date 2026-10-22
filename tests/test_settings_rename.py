"""The product is Convox (was dubhash): CONVOX_ENV is the setting, and an old .env with
DUBHASH_ENV keeps working."""

from convox.config.settings import Settings


def test_convox_env_is_read(monkeypatch):
    monkeypatch.setenv("CONVOX_ENV", "prod")
    assert Settings(_env_file=None).convox_env == "prod"


def test_old_dubhash_env_still_works(monkeypatch):
    monkeypatch.delenv("CONVOX_ENV", raising=False)
    monkeypatch.setenv("DUBHASH_ENV", "prod")
    assert Settings(_env_file=None).convox_env == "prod"


def test_convox_env_wins_over_the_old_name(monkeypatch):
    monkeypatch.setenv("CONVOX_ENV", "test")
    monkeypatch.setenv("DUBHASH_ENV", "prod")
    assert Settings(_env_file=None).convox_env == "test"


def test_keyword_still_sets_it():
    assert Settings(_env_file=None, convox_env="test").convox_env == "test"
