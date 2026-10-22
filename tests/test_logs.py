import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler

import pytest

from convox.config.settings import Settings
from convox.logs import call_id_var, configure_file_logging, remove_file_logging, setup_logging


@pytest.fixture
def clean():
    yield
    remove_file_logging()


def read(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_writes_json_lines_with_call_id_from_context_or_extra(tmp_path, clean):
    path = tmp_path / "logs" / "convox.log"
    handler = configure_file_logging(path, "INFO")
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == 10 * 1024 * 1024 and handler.backupCount == 5
    log = logging.getLogger("convox.test")

    async def in_call():
        call_id_var.set("c_ctx")
        log.info("floor.state", extra={"side": "a", "queue_depth": 1})

    asyncio.run(in_call())
    log.warning("leg.gap", extra={"call_id": "c_extra", "gap_ms": 200})
    log.info("no.call")
    handler.flush()
    rows = read(path)
    assert rows[0]["event"] == "floor.state" and rows[0]["call_id"] == "c_ctx"
    assert rows[0]["side"] == "a" and rows[0]["queue_depth"] == 1 and rows[0]["level"] == "INFO"
    assert rows[0]["logger"] == "convox.test" and rows[0]["ts"].endswith("Z")
    assert rows[1]["call_id"] == "c_extra" and rows[1]["gap_ms"] == 200
    assert "call_id" not in rows[2]


def test_is_idempotent_and_appends_across_restarts(tmp_path, clean):
    path = tmp_path / "convox.log"
    configure_file_logging(path, "INFO")
    configure_file_logging(path, "INFO")
    root = logging.getLogger()
    assert sum(getattr(h, "_convox_file", False) for h in root.handlers) == 1
    logging.getLogger("convox.x").info("first")
    remove_file_logging()  # "restart"
    configure_file_logging(path, "INFO")
    logging.getLogger("convox.x").info("second")
    remove_file_logging()
    assert [r["event"] for r in read(path)] == ["first", "second"]


def test_exceptions_are_logged_as_text(tmp_path, clean):
    path = tmp_path / "d.log"
    configure_file_logging(path, "INFO")
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("convox.x").exception("session.failed")
    remove_file_logging()
    (row,) = read(path)
    assert "ValueError: boom" in row["exc"]


def test_setup_logging_follows_settings(tmp_path, clean):
    off = Settings(_env_file=None, convox_env="test", log_file=tmp_path / "t.log")
    assert setup_logging(off) is None
    assert setup_logging(Settings(_env_file=None, convox_env="dev", log_file="")) is None
    on = Settings(_env_file=None, convox_env="dev", log_file=tmp_path / "d.log")
    assert setup_logging(on) is not None
