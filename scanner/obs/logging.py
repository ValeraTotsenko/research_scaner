"""
Structured JSON Lines logging for observability.

This module provides structured logging capabilities with machine-parseable
JSON Lines (JSONL) format output. Each log entry includes:
- Timestamp (ISO 8601 UTC)
- Log level
- Run ID for correlation
- Event type for filtering
- Module name
- Human-readable message
- Extra structured data

The JSONL format enables easy log analysis with tools like jq,
and integration with log aggregation systems.

Example log entry:
    {"ts": "2024-01-15T10:30:00Z", "level": "INFO", "run_id": "abc123",
     "event": "stage_started", "module": "runner", "msg": "Starting universe",
     "extra": {"symbol_count": 150}}
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LogSettings:
    """
    Configuration for logger initialization.

    Attributes:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        run_id: Unique run identifier for log correlation.
        log_file: Optional path to log file (None for console only).
        jsonl: If True, use JSON Lines format; otherwise plain text.
    """
    level: str
    run_id: str
    log_file: Path | None
    jsonl: bool


class JsonLineFormatter(logging.Formatter):
    """
    Logging formatter that outputs JSON Lines format.

    Each log record is formatted as a single JSON object per line,
    including structured metadata for machine processing.
    """

    def __init__(self, run_id: str):
        """
        Initialize formatter with run ID for correlation.

        Args:
            run_id: Run identifier included in all log entries.
        """
        super().__init__()
        self._run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON object."""
        event = getattr(record, "event", "log")
        extra = getattr(record, "extra", {})
        if not isinstance(extra, dict):
            extra = {"value": extra}

        payload = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "run_id": self._run_id,
            "event": event,
            "module": record.module,
            "msg": record.getMessage(),
            "extra": extra,
        }
        return json.dumps(payload, ensure_ascii=False)


def build_logger(settings: LogSettings) -> logging.Logger:
    """
    Create and configure a logger instance for a scanner run.

    Creates an isolated logger (non-propagating) with handlers for
    console and optionally file output. Uses JSON Lines format when
    settings.jsonl is True.

    Args:
        settings: LogSettings with level, run_id, file path, and format.

    Returns:
        Configured Logger instance ready for use.
    """
    logger = logging.getLogger(f"scanner.{settings.run_id}")
    logger.setLevel(settings.level)
    logger.handlers.clear()
    logger.propagate = False

    formatter = JsonLineFormatter(settings.run_id) if settings.jsonl else None

    # Console output handler
    stream_handler = logging.StreamHandler()
    if formatter:
        stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # File output handler (optional)
    if settings.log_file:
        file_handler = logging.FileHandler(settings.log_file, encoding="utf-8")
        if formatter:
            file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    *,
    exc_info: logging._ExcInfoType | None = None,
    **extra: Any,
) -> None:
    """
    Log a structured event with typed metadata.

    Convenience function for emitting structured log entries with
    an event type for filtering and additional key-value metadata.

    Args:
        logger: Logger instance to use.
        level: Log level (logging.DEBUG, INFO, WARNING, ERROR).
        event: Event type identifier (e.g., "stage_started", "api_error").
        message: Human-readable log message.
        exc_info: Optional exception info for error logging.
        **extra: Additional key-value pairs to include in log entry.

    Example:
        >>> log_event(logger, logging.INFO, "spread_sampled",
        ...           "Spread sample collected", symbol="BTCUSDT", spread_bps=15.2)
    """
    logger.log(level, message, extra={"event": event, "extra": extra}, exc_info=exc_info)
