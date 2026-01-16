from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_hex
from subprocess import CalledProcessError, check_output

from scanner import __version__
from scanner.config import ConfigError, load_config
from scanner.io.layout import ensure_run_layout, write_run_meta
from scanner.obs.logging import LogSettings, build_logger, log_event
from scanner.obs.metrics import summarize_api_health, update_metrics
from scanner.pipeline.runner import (
    EXIT_VALIDATION_ERROR,
    PipelineOptions,
    build_stage_plan,
    run_pipeline,
)
from scanner.pipeline.state import PIPELINE_SPEC_VERSION


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research scanner CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run full pipeline")
    run_parser.add_argument("--config", required=True, help="Path to config YAML")
    run_parser.add_argument("--output", required=True, help="Output directory")
    run_parser.add_argument("--run-id", help="Resume an existing run_id if provided")
    run_parser.add_argument("--dry-run", action="store_true", help="Validate plan and artifacts only")
    run_parser.add_argument("--log-level", default="INFO", help="Logging level")
    run_parser.add_argument("--from", dest="stage_from", help="Start stage")
    run_parser.add_argument("--to", dest="stage_to", help="End stage")
    run_parser.add_argument("--stages", help="Comma-separated stage list")
    run_parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    run_parser.add_argument("--force", action="store_true", default=False)
    run_parser.add_argument("--fail-fast", action=argparse.BooleanOptionalAction, default=True)
    run_parser.add_argument("--continue-on-error", action="store_true", default=False)
    run_parser.add_argument("--artifact-validation", choices=["strict", "lenient"])

    cleanup_parser = subparsers.add_parser("cleanup", help="Remove old run artifacts")
    cleanup_parser.add_argument("--output", required=True, help="Output directory")
    cleanup_parser.add_argument("--keep-days", type=int, default=7, help="Days to keep artifacts")
    cleanup_parser.add_argument("--keep-last", type=int, default=20, help="Always keep last N runs")
    cleanup_parser.add_argument("--dry-run", action="store_true", help="Preview removals only")
    cleanup_parser.add_argument("--verbose", action="store_true", help="Print kept artifacts")

    return parser.parse_args(argv)


def generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    suffix = token_hex(3)
    return f"{timestamp}_{suffix}"


def get_git_commit() -> str | None:
    try:
        return check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (CalledProcessError, FileNotFoundError):
        return None


def ensure_output_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise PermissionError(f"Cannot create output directory: {path}") from exc


def _parse_stage_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [stage.strip() for stage in value.split(",") if stage.strip()]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.command == "cleanup":
        from scanner.cleanup import cleanup_output

        try:
            return cleanup_output(
                Path(args.output),
                keep_days=args.keep_days,
                keep_last=args.keep_last,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
        except ValueError as exc:
            print(str(exc))
            return 2
    if args.command != "run":
        raise ValueError(f"Unsupported command: {args.command}")

    output_dir = Path(args.output)
    run_id = args.run_id or generate_run_id()
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    logger = build_logger(
        LogSettings(
            level=args.log_level.upper(),
            run_id=run_id,
            log_file=None,
            jsonl=True,
        )
    )

    try:
        ensure_output_dir(output_dir)
    except PermissionError as exc:
        log_event(logger, 40, "output_not_writable", str(exc))
        return 1

    try:
        loaded = load_config(Path(args.config))
    except ConfigError as exc:
        log_event(logger, 40, "config_invalid", str(exc))
        return 2

    try:
        layout = ensure_run_layout(output_dir, run_id, loaded.config)
    except (PermissionError, FileExistsError) as exc:
        log_event(logger, 40, "output_not_writable", str(exc))
        return 1

    if layout.log_path:
        logger = build_logger(
            LogSettings(
                level=args.log_level.upper(),
                run_id=run_id,
                log_file=layout.log_path,
                jsonl=True,
            )
        )

    if layout.run_meta_path.exists():
        try:
            payload = json.loads(layout.run_meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log_event(logger, 40, "run_meta_invalid", str(exc))
            return EXIT_VALIDATION_ERROR
        if payload.get("spec_version") != PIPELINE_SPEC_VERSION:
            log_event(
                logger,
                40,
                "run_meta_incompatible",
                "Spec version mismatch; clean run folder / rerun",
                current=PIPELINE_SPEC_VERSION,
                existing=payload.get("spec_version"),
            )
            return EXIT_VALIDATION_ERROR

    write_run_meta(
        layout.run_meta_path,
        run_id=run_id,
        started_at=started_at,
        git_commit=get_git_commit(),
        config=loaded.config.model_dump(mode="json"),
        status="running",
        run_health="ok",
        scanner_version=__version__,
        spec_version=PIPELINE_SPEC_VERSION,
    )

    try:
        stage_plan = build_stage_plan(
            selected_stages=_parse_stage_list(args.stages),
            stage_from=args.stage_from,
            stage_to=args.stage_to,
        )
    except ValueError as exc:
        log_event(logger, 40, "config_invalid", str(exc))
        return 2

    options = PipelineOptions(
        resume=args.resume,
        force=args.force,
        fail_fast=args.fail_fast,
        continue_on_error=args.continue_on_error,
        dry_run=args.dry_run,
        artifact_validation=args.artifact_validation or loaded.config.pipeline.artifact_validation,
    )

    log_event(logger, 20, "run_started", "Run initialized", dry_run=args.dry_run)
    exit_code = run_pipeline(
        run_dir=layout.run_dir,
        run_id=run_id,
        config=loaded.config,
        logger=logger,
        metrics_path=layout.metrics_path,
        stage_plan=stage_plan,
        options=options,
    )

    status = "success" if exit_code == 0 else "failed"
    metrics_payload: dict[str, object] = {}
    if layout.metrics_path.exists():
        raw_metrics = layout.metrics_path.read_text(encoding="utf-8").strip()
        if raw_metrics:
            metrics_payload = json.loads(raw_metrics)
    health_summary = summarize_api_health(metrics_payload)
    run_health = str(health_summary.get("run_health", "ok"))
    run_degraded = 0 if run_health == "ok" else 1
    update_metrics(layout.metrics_path, gauges={"run_degraded": run_degraded})
    write_run_meta(
        layout.run_meta_path,
        run_id=run_id,
        started_at=started_at,
        git_commit=get_git_commit(),
        config=loaded.config.model_dump(mode="json"),
        status=status,
        run_health=run_health,
        scanner_version=__version__,
        spec_version=PIPELINE_SPEC_VERSION,
        error=None if exit_code == 0 else f"pipeline_exit_{exit_code}",
    )

    log_event(logger, 20, "run_complete", "Run complete", exit_code=exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
