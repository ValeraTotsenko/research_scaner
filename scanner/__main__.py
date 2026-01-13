from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_hex
from subprocess import CalledProcessError, check_output

from scanner.config import ConfigError, load_config
from scanner.io.layout import create_run_layout, write_run_meta
from scanner.obs.logging import LogSettings, build_logger, log_event


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research scanner bootstrap")
    parser.add_argument("--config", required=True, help="Path to config YAML")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--dry-run", action="store_true", help="Validate config and layout only")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    output_dir = Path(args.output)
    run_id = generate_run_id()
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
        run_dir = output_dir / f"run_{run_id}"
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            write_run_meta(
                run_dir / "run_meta.json",
                run_id=run_id,
                started_at=started_at,
                git_commit=get_git_commit(),
                config=None,
                status="failed",
                error=str(exc),
            )
        except PermissionError as perm_exc:
            log_event(logger, 40, "run_meta_failed", str(perm_exc))
            return 1
        return 2

    try:
        layout = create_run_layout(output_dir, run_id, loaded.config)
    except PermissionError as exc:
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

    write_run_meta(
        layout.run_meta_path,
        run_id=run_id,
        started_at=started_at,
        git_commit=get_git_commit(),
        config=loaded.raw,
        status="success",
    )

    log_event(logger, 20, "run_started", "Run initialized", dry_run=args.dry_run)

    if args.dry_run:
        log_event(logger, 20, "dry_run", "Dry run complete")
        return 0

    log_event(logger, 20, "run_complete", "Run complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
