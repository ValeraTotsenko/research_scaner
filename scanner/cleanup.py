from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SECONDS_IN_DAY = 24 * 60 * 60


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    modified_at: datetime


@dataclass
class CleanupSummary:
    removed: list[Path]
    kept: list[Path]
    skipped: list[Path]


def _list_run_dirs(output_dir: Path) -> list[CleanupCandidate]:
    candidates: list[CleanupCandidate] = []
    for entry in output_dir.iterdir():
        if not entry.is_dir():
            continue
        if not entry.name.startswith("run_"):
            continue
        modified_at = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
        candidates.append(CleanupCandidate(path=entry, modified_at=modified_at))
    return candidates


def _select_removals(
    candidates: list[CleanupCandidate],
    *,
    keep_days: int,
    keep_last: int,
    now: datetime,
) -> CleanupSummary:
    ordered = sorted(candidates, key=lambda item: item.modified_at, reverse=True)
    keep_set = {item.path for item in ordered[:keep_last]} if keep_last > 0 else set()

    removed: list[Path] = []
    kept: list[Path] = []
    skipped: list[Path] = []

    for item in ordered:
        if item.path in keep_set:
            kept.append(item.path)
            continue

        age_seconds = (now - item.modified_at).total_seconds()
        age_days = age_seconds / SECONDS_IN_DAY
        if age_days > keep_days:
            removed.append(item.path)
        else:
            skipped.append(item.path)

    return CleanupSummary(removed=removed, kept=kept, skipped=skipped)


def cleanup_output(
    output_dir: Path,
    *,
    keep_days: int,
    keep_last: int,
    dry_run: bool,
    verbose: bool = False,
    now: datetime | None = None,
) -> int:
    if keep_days < 0 or keep_last < 0:
        raise ValueError("keep-days and keep-last must be non-negative")

    if not output_dir.exists():
        print(f"Output directory does not exist: {output_dir}")
        return 1

    candidates = _list_run_dirs(output_dir)
    if not candidates:
        if verbose:
            print(f"No run directories found in {output_dir}")
        return 0

    summary = _select_removals(
        candidates,
        keep_days=keep_days,
        keep_last=keep_last,
        now=now or datetime.now(timezone.utc),
    )

    for path in summary.removed:
        if dry_run:
            print(f"DRY-RUN remove {path}")
            continue
        try:
            shutil.rmtree(path)
            print(f"Removed {path}")
        except OSError as exc:
            print(f"Failed to remove {path}: {exc}")
            return 1

    if verbose:
        for path in summary.kept:
            print(f"Kept (recent) {path}")
        for path in summary.skipped:
            print(f"Kept (within {keep_days} days) {path}")

    print(
        "Cleanup summary: "
        f"removed={len(summary.removed)}, "
        f"kept={len(summary.kept)}, "
        f"skipped={len(summary.skipped)}"
    )
    return 0
