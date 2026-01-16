from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scanner.io.summary_export import SUMMARY_COLUMNS


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    error: str | None = None


def _csv_has_columns(path: Path, columns: Iterable[str], *, require_rows: bool) -> ValidationResult:
    if not path.exists():
        return ValidationResult(False, f"Missing file: {path.name}")

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return ValidationResult(False, f"Missing CSV header: {path.name}")
        missing = [col for col in columns if col not in reader.fieldnames]
        if missing:
            return ValidationResult(False, f"Missing columns in {path.name}: {', '.join(missing)}")
        if require_rows:
            if next(reader, None) is None:
                return ValidationResult(False, f"CSV has no rows: {path.name}")

    return ValidationResult(True, None)


def validate_universe(path: Path, *, strict: bool) -> ValidationResult:
    if not path.exists():
        return ValidationResult(False, f"Missing file: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ValidationResult(False, f"Invalid JSON in {path.name}: {exc}")

    if not isinstance(payload, dict):
        return ValidationResult(False, f"Universe payload must be a dict: {path.name}")
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        return ValidationResult(False, f"Universe symbols must be a list: {path.name}")
    if strict and not symbols:
        return ValidationResult(False, f"Universe symbols empty: {path.name}")

    return ValidationResult(True, None)


def validate_summary_csv(path: Path, *, strict: bool) -> ValidationResult:
    return _csv_has_columns(path, SUMMARY_COLUMNS, require_rows=strict)


def validate_depth_metrics(path: Path, *, band_bps: Iterable[int], strict: bool) -> ValidationResult:
    required = [
        "symbol",
        "sample_count",
        "valid_samples",
        "empty_book_count",
        "invalid_book_count",
        "symbol_unavailable_count",
        "best_bid_notional_median",
        "best_ask_notional_median",
        "topn_bid_notional_median",
        "topn_ask_notional_median",
        "unwind_slippage_p90_bps",
        "uptime",
        "best_bid_notional_pass",
        "best_ask_notional_pass",
        "unwind_slippage_pass",
        "band_10bps_notional_pass",
        "topn_notional_pass",
        "pass_depth",
        "depth_fail_reasons",
    ]
    band_cols = [f"band_bid_notional_median_{band}bps" for band in band_bps]
    columns = required[:10] + band_cols + required[10:]
    return _csv_has_columns(path, columns, require_rows=strict)


def validate_report_md(path: Path, *, strict: bool) -> ValidationResult:
    if not path.exists():
        return ValidationResult(False, f"Missing file: {path.name}")
    if strict:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return ValidationResult(False, f"Report is empty: {path.name}")
    return ValidationResult(True, None)
