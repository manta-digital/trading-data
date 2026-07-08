"""Structured logging configuration for the manta-trading framework."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from manta_trading.config import Settings


class _JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


_TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# Third-party loggers whose default INFO output leaks credentials in
# request URLs (e.g. httpx logs the full GET URL including any
# `api_token=...` query parameter). Pinned to WARNING so accidental key
# disclosure cannot happen via these channels regardless of our own
# log_level setting.
_CREDENTIAL_SAFE_LOGGERS = ("httpx", "httpcore")


def setup_logging(settings: Settings) -> None:
    """Configure the root logger from *settings*.

    Idempotent — calling it multiple times with the same settings is safe.
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers to avoid duplicate output.
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    if settings.log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT))

    root.addHandler(handler)

    # Suppress URL-logging from third-party HTTP clients that emit the
    # full request URL (including query-string credentials) at INFO.
    for noisy in _CREDENTIAL_SAFE_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger.

    Callers must invoke :func:`setup_logging` once at application startup
    before using loggers in production code.
    """
    return logging.getLogger(name)
