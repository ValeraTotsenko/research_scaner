from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scanner.config import AppConfig


@dataclass(frozen=True)
class RunLayout:
    run_dir: Path
    log_path: Path | None
    run_meta_path: Path
    metrics_path: Path


def create_run_layout(output_dir: Path, run_id: str, config: AppConfig) -> RunLayout:
    run_dir = output_dir / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)

    log_path = run_dir / "logs.jsonl" if config.obs.log_jsonl else None
    if log_path:
        log_path.touch(exist_ok=False)

    run_meta_path = run_dir / "run_meta.json"
    metrics_path = run_dir / "metrics.json"

    metrics_payload = {
        "requests_total": 0,
        "errors_total": 0,
        "retries_total": 0,
        "requests_by_status": {},
        "latency_ms": {"count": 0, "min": None, "max": None, "buckets": {}},
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    metrics_path.write_text(json.dumps(metrics_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return RunLayout(
        run_dir=run_dir,
        log_path=log_path,
        run_meta_path=run_meta_path,
        metrics_path=metrics_path,
    )


def ensure_run_layout(output_dir: Path, run_id: str, config: AppConfig) -> RunLayout:
    run_dir = output_dir / f"run_{run_id}"
    if not run_dir.exists():
        return create_run_layout(output_dir, run_id, config)

    log_path = run_dir / "logs.jsonl" if config.obs.log_jsonl else None
    if log_path:
        log_path.touch(exist_ok=True)

    run_meta_path = run_dir / "run_meta.json"
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        metrics_payload = {
            "requests_total": 0,
            "errors_total": 0,
            "retries_total": 0,
            "requests_by_status": {},
            "latency_ms": {"count": 0, "min": None, "max": None, "buckets": {}},
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        metrics_path.write_text(json.dumps(metrics_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return RunLayout(
        run_dir=run_dir,
        log_path=log_path,
        run_meta_path=run_meta_path,
        metrics_path=metrics_path,
    )


def write_run_meta(
    path: Path,
    *,
    run_id: str,
    started_at: str,
    git_commit: str | None,
    config: dict[str, Any] | None,
    status: str,
    scanner_version: str,
    spec_version: str,
    error: str | None = None,
) -> None:
    config_payload = config or {}
    config_hash = None
    if config is not None:
        normalized = json.dumps(config_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        config_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    payload: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_at,
        "git_commit": git_commit,
        "config": config_payload,
        "config_hash": config_hash,
        "status": status,
        "scanner_version": scanner_version,
        "spec_version": spec_version,
    }
    if error:
        payload["error"] = error

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
