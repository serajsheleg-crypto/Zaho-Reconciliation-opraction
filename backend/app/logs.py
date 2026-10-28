"""Server log file + the latest problems, so they can be read on the System page instead of a console window."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import settings

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _Recent(logging.Handler):
    """Keeps the last warnings/errors in memory (newest first when read)."""

    def __init__(self, size: int = 50):
        super().__init__(logging.WARNING)
        self.items: deque[dict] = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage().splitlines()[0][:400] if record.getMessage() else ""
            error = None
            if record.exc_info and record.exc_info[1] is not None:
                exc = record.exc_info[1]
                error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:300] if str(exc) else ''}"
            elif "Traceback" in record.getMessage():
                lines = [x for x in record.getMessage().splitlines() if x.strip()]
                error = lines[-1][:300] if lines else None
            self.items.append({
                "at": datetime.fromtimestamp(record.created).isoformat(sep=" ", timespec="seconds"),
                "level": record.levelname, "source": record.name, "message": message, "error": error,
            })
        except Exception:  # logging must never break a request
            self.handleError(record)


_recent = _Recent()
_file: RotatingFileHandler | None = None


def log_file() -> Path:
    return settings.path("logs") / "server.log"


def setup() -> None:
    """Root logger → console + data/logs/server.log (1 MB × 3) + the in-memory list. Safe to call again
    (uvicorn re-configures its own loggers after `python -m app.main` first imported this)."""
    global _file
    logging.basicConfig(level=logging.INFO, format=FORMAT)
    if _file is None:
        _file = RotatingFileHandler(log_file(), maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
        _file.setFormatter(logging.Formatter(FORMAT))
        _file.setLevel(logging.INFO)
    # uvicorn's own error logger does not propagate to the root logger.
    for logger in (logging.getLogger(), logging.getLogger("uvicorn.error")):
        for handler in (_file, _recent):
            if handler not in logger.handlers:
                logger.addHandler(handler)


def recent(limit: int = 20) -> list[dict]:
    return list(reversed(_recent.items))[:limit]
