"""api/logging_setup.py — Readable log format + per-request correlation ID."""

import logging
import logging.handlers
import os
from contextvars import ContextVar

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

_LOG_RECORD_BUILTINS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime"}


class _ReadableFormatter(logging.Formatter):
    LEVEL_ICONS = {"DEBUG": "·", "INFO": "✓", "WARNING": "!", "ERROR": "✗", "CRITICAL": "✗✗"}

    def format(self, record: logging.LogRecord) -> str:
        icon  = self.LEVEL_ICONS.get(record.levelname, "·")
        extra = {k: v for k, v in record.__dict__.items()
                 if k not in _LOG_RECORD_BUILTINS and not k.startswith("_")}
        extra_str = "  " + "  ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
        line = f"{icon} {record.getMessage()}{extra_str}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(log_file: str = "") -> logging.Logger:
    fmt     = _ReadableFormatter()
    handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    root          = logging.getLogger()
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
