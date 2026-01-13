from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LogSettings:
    level: str
    run_id: str
    log_file: Path | None
    jsonl: bool


class JsonLineFormatter(logging.Formatter):
    def __init__(self, run_id: str):
        super().__init__()
        self._run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
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
    logger = logging.getLogger(f"scanner.{settings.run_id}")
    logger.setLevel(settings.level)
    logger.handlers.clear()
    logger.propagate = False

    formatter = JsonLineFormatter(settings.run_id) if settings.jsonl else None

    stream_handler = logging.StreamHandler()
    if formatter:
        stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if settings.log_file:
        file_handler = logging.FileHandler(settings.log_file, encoding="utf-8")
        if formatter:
            file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def log_event(logger: logging.Logger, level: int, event: str, message: str, **extra: Any) -> None:
    logger.log(level, message, extra={"event": event, "extra": extra})
