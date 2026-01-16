from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scanner.mexc.client import MexcMetrics

_LATENCY_BUCKETS_MS = (25, 50, 100, 250, 500, 1000, 2000, 5000)


def _read_metrics(metrics_path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if metrics_path.exists():
        raw = metrics_path.read_text(encoding="utf-8").strip()
        if raw:
            payload = json.loads(raw)
    return payload


def _write_metrics(metrics_path: Path, payload: dict[str, Any]) -> None:
    metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def update_metrics(
    metrics_path: Path,
    *,
    increments: dict[str, int] | None = None,
    gauges: dict[str, int | float] | None = None,
) -> None:
    payload = _read_metrics(metrics_path)

    if increments:
        for key, value in increments.items():
            payload[key] = int(payload.get(key, 0)) + value

    if gauges:
        for key, value in gauges.items():
            payload[key] = value

    _write_metrics(metrics_path, payload)


def update_http_metrics(metrics_path: Path, metrics: MexcMetrics) -> None:
    payload = _read_metrics(metrics_path)

    requests_total = sum(metrics.http_requests_total.values())
    retries_total = sum(metrics.http_retries_total.values())
    requests_by_status: dict[str, int] = {}
    errors_total = 0
    for (_endpoint, status), count in metrics.http_requests_total.items():
        requests_by_status[status] = requests_by_status.get(status, 0) + count
        try:
            status_code = int(status)
        except (TypeError, ValueError):
            errors_total += count
        else:
            if not 200 <= status_code < 300:
                errors_total += count

    http_429_total = requests_by_status.get("429", 0)
    http_403_total = requests_by_status.get("403", 0)
    http_5xx_total = 0
    for status, count in requests_by_status.items():
        try:
            status_code = int(status)
        except (TypeError, ValueError):
            continue
        if 500 <= status_code <= 599:
            http_5xx_total += count

    latencies = [value for values in metrics.http_latency_ms.values() for value in values]
    buckets: dict[str, int] = {}
    for bound in _LATENCY_BUCKETS_MS:
        buckets[str(bound)] = sum(1 for value in latencies if value <= bound)
    buckets["+inf"] = len(latencies)

    payload.update(
        {
            "requests_total": requests_total,
            "errors_total": errors_total,
            "retries_total": retries_total,
            "requests_by_status": requests_by_status,
            "http_429_total": http_429_total,
            "http_403_total": http_403_total,
            "http_5xx_total": http_5xx_total,
            "latency_ms": {
                "count": len(latencies),
                "min": min(latencies) if latencies else None,
                "max": max(latencies) if latencies else None,
                "buckets": buckets,
            },
        }
    )

    _write_metrics(metrics_path, payload)


def summarize_api_health(payload: dict[str, Any]) -> dict[str, int | str]:
    http_429_total = int(payload.get("http_429_total") or 0)
    http_403_total = int(payload.get("http_403_total") or 0)
    http_5xx_total = int(payload.get("http_5xx_total") or 0)
    run_degraded = int(payload.get("run_degraded") or 0)

    if http_5xx_total > 0:
        run_health = "api_unstable"
    elif http_429_total > 0 or http_403_total > 0 or run_degraded > 0:
        run_health = "degraded"
    else:
        run_health = "ok"

    return {
        "run_health": run_health,
        "http_429_total": http_429_total,
        "http_403_total": http_403_total,
        "http_5xx_total": http_5xx_total,
        "run_degraded": run_degraded,
    }
