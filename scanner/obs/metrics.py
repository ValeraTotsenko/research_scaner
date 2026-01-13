from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def update_metrics(
    metrics_path: Path,
    *,
    increments: dict[str, int] | None = None,
    gauges: dict[str, int | float] | None = None,
) -> None:
    payload: dict[str, Any] = {}
    if metrics_path.exists():
        raw = metrics_path.read_text(encoding="utf-8").strip()
        if raw:
            payload = json.loads(raw)

    if increments:
        for key, value in increments.items():
            payload[key] = int(payload.get(key, 0)) + value

    if gauges:
        for key, value in gauges.items():
            payload[key] = value

    metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
