from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path

from scanner.config import AppConfig
from scanner.obs.logging import log_event
from scanner.obs.metrics import update_metrics


BUNDLE_FILES = (
    "summary.csv",
    "summary.json",
    "depth_metrics.csv",
    "summary_enriched.csv",
    "run_meta.json",
    "report.md",
    "shortlist.csv",
)


def _iter_raw_files(run_dir: Path) -> list[Path]:
    raw_files: list[Path] = []
    for path in run_dir.iterdir():
        if path.is_file() and path.name.startswith("raw_bookticker"):
            raw_files.append(path)
    return raw_files


def create_run_bundle(run_dir: Path, cfg: AppConfig) -> Path:
    run_meta_path = run_dir / "run_meta.json"
    if not run_meta_path.exists():
        raise FileNotFoundError(f"run_meta.json not found in {run_dir}")

    run_meta = json.loads(run_meta_path.read_text(encoding="utf-8"))
    bundle_path = run_dir / "run_bundle.zip"

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for filename in BUNDLE_FILES:
            path = run_dir / filename
            if path.exists():
                bundle.write(path, arcname=filename)

        config_payload = run_meta.get("config", {})
        bundle.writestr("run_config.json", json.dumps(config_payload, ensure_ascii=False, indent=2))

        if cfg.report.include_raw_in_bundle:
            for raw_path in _iter_raw_files(run_dir):
                bundle.write(raw_path, arcname=raw_path.name)

    metrics_path = run_dir / "metrics.json"
    update_metrics(metrics_path, increments={"bundle_created_total": 1})

    logger = logging.getLogger(__name__)
    log_event(
        logger,
        logging.INFO,
        "bundle_created",
        "Run bundle created",
        path=str(bundle_path),
    )

    return bundle_path
