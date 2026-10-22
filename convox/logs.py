"""App logging: JSON lines to a rotating file (settings.log_file, default
logs/convox.log, 10 MB x 5 files, appended across restarts, never truncated), next to
whatever stdout handler uvicorn sets up.

Every line carries `call_id` when known: from the record's `extra`, else from the
`call_id_var` context variable. The leg websocket handler sets it before attaching a leg,
and the call's tasks (created from that context) inherit it.

    {"ts": "2026-10-21T10:00:00.123Z", "level": "INFO", "logger": "convox.session.floor",
     "event": "floor.state", "call_id": "c_ab12", "from": "IDLE", "to": "PLAYING_AB"}
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from convox.config.settings import Settings

call_id_var: ContextVar[str | None] = ContextVar("convox_call_id", default=None)

MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5
# uvicorn's own loggers do not propagate to the root logger
_TARGETS = ("", "uvicorn", "uvicorn.access")
_STD = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, UTC)
        out: dict[str, Any] = {
            "ts": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        call_id = getattr(record, "call_id", None) or call_id_var.get()
        if call_id:
            out["call_id"] = call_id
        for k, v in record.__dict__.items():
            if k not in _STD and k not in out and k != "call_id":
                out[k] = v if isinstance(v, str | int | float | bool | type(None)) else repr(v)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def _ours(h: logging.Handler) -> bool:
    return bool(getattr(h, "_convox_file", False))


def configure_file_logging(path: Path | str, level: str = "INFO") -> RotatingFileHandler:
    """Add the JSON file handler (once per process) and let convox.* log at `level`."""
    path = Path(path)
    for h in logging.getLogger().handlers:
        if _ours(h) and isinstance(h, RotatingFileHandler):
            if Path(h.baseFilename) == path.resolve():
                return h
            remove_file_logging()  # a different file: replace it
            break
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path, mode="a", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler._convox_file = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter())
    for name in _TARGETS:
        logging.getLogger(name).addHandler(handler)
    app = logging.getLogger("convox")
    want = logging.getLevelName(level.upper())
    if isinstance(want, int) and (app.level == logging.NOTSET or app.level > want):
        app.setLevel(want)
    return handler


def remove_file_logging() -> None:
    for name in _TARGETS:
        lg = logging.getLogger(name)
        for h in [h for h in lg.handlers if _ours(h)]:
            lg.removeHandler(h)
            h.close()


def setup_logging(settings: Settings) -> RotatingFileHandler | None:
    """The app's file log, unless disabled (empty LOG_FILE) or under CONVOX_ENV=test."""
    if settings.log_file is None or settings.convox_env == "test":
        return None
    return configure_file_logging(settings.log_file, settings.log_level)
