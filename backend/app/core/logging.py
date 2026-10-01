"""Structured logging configuration.

Investor-safety investigations must be auditable, so every log line carries the
investigation context when one exists. Logging is configured once, at app
startup, and never from inside request handlers.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging with a single stream handler.

    Args:
        level: Logging level name (e.g. ``INFO``, ``DEBUG``).
    """
    resolved = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(resolved)

    # Replace our own handlers only; never touch handlers other libraries own.
    for handler in [h for h in root.handlers if getattr(h, "_investshield", False)]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    handler._investshield = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    # These are chatty and add nothing to an investigation audit trail.
    logging.getLogger("httpx").setLevel(max(resolved, logging.WARNING))
    logging.getLogger("httpcore").setLevel(max(resolved, logging.WARNING))


def get_logger(name: str) -> logging.Logger:
    """Return a module logger.

    Args:
        name: Usually ``__name__``.

    Returns:
        Configured logger instance.
    """
    return logging.getLogger(name)


def log_extra(**fields: Any) -> dict[str, Any]:
    """Build a structured ``extra=`` payload for log records.

    Args:
        **fields: Arbitrary key/value context to attach to a log record.

    Returns:
        Mapping suitable for ``logger.info(msg, extra=log_extra(...))``.
    """
    return {"investshield": fields}


__all__ = ["DATE_FORMAT", "LOG_FORMAT", "configure_logging", "get_logger", "log_extra"]
