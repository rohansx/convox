from pathlib import Path

from convox.config.settings import Settings


def test_call_recording_defaults_on_in_dev_only(monkeypatch):
    monkeypatch.delenv("CALL_RECORDING", raising=False)
    assert Settings(_env_file=None, convox_env="dev").call_recording is True
    assert Settings(_env_file=None, convox_env="test").call_recording is False
    assert Settings(_env_file=None, convox_env="prod").call_recording is False


def test_call_recording_env_overrides_the_default(monkeypatch):
    monkeypatch.setenv("CALL_RECORDING", "false")
    assert Settings(_env_file=None, convox_env="dev").call_recording is False
    monkeypatch.setenv("CALL_RECORDING", "true")
    assert Settings(_env_file=None, convox_env="test").call_recording is True


def test_call_recording_dir_and_log_file_defaults(monkeypatch):
    monkeypatch.delenv("CALL_RECORDING_DIR", raising=False)
    monkeypatch.delenv("LOG_FILE", raising=False)
    s = Settings(_env_file=None)
    assert s.call_recording_dir == Path("out/calls")
    assert s.log_file == Path("logs/convox.log")
