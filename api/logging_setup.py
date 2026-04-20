"""api/logging_setup.py — Structured JSON logging + per-request correlation ID."""

import json
import logging
import logging.handlers
import os
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# Per-request context variable — set by middleware, read by log formatter
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

_LOG_RECORD_BUILTINS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime"}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts":             datetime.now(timezone.utc).isoformat(),
            "level":          record.levelname,
            "logger":         record.name,
            "correlation_id": correlation_id.get("-"),
            "message":        record.getMessage(),
        }
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        for key, val in record.__dict__.items():
            if key not in _LOG_RECORD_BUILTINS and not key.startswith("_"):
                payload[key] = val
        return json.dumps(payload)


def setup_logging(log_file: str = "") -> logging.Logger:
    fmt     = _JsonFormatter()
    handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    root         = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        fh.setLevel(logging.ERROR)
        fh.setFormatter(fmt)
        root.addHandler(fh)

    return logging.getLogger("tourai.api")
