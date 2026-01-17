"""
Summary export module for spread analysis results.

This module exports ScoreResult objects to CSV and JSON formats for reporting
and further analysis. The summary files contain comprehensive spread statistics,
edge metrics, and pass/fail information for each analyzed symbol.

Key Exports:
    - summary.csv: Human-readable CSV with all spread metrics and edge calculations
    - summary.json: Machine-readable JSON with same data plus nested fail_reasons arrays

New Fields (v0.1.1):
    - used_quote_volume_estimate: Boolean flag indicating if quote volume was estimated
    - trade_count_missing: Boolean flag indicating if trade count data was unavailable
    - edge_mm_p25_bps: Pessimistic maker/maker edge using P25 spread
    - edge_mt_bps: Maker/taker edge for emergency unwind scenarios

Example:
    >>> from pathlib import Path
    >>> from scanner.io.summary_export import export_summary
    >>> results = [score_result1, score_result2, ...]
    >>> paths = export_summary(Path("./output/run_123"), results)
    >>> print(f"CSV: {paths.csv_path}, JSON: {paths.json_path}")
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scanner.analytics.scoring import ScoreResult
from scanner.obs.logging import log_event


@dataclass(frozen=True)
class SummaryExportPaths:
    csv_path: Path
    json_path: Path


SUMMARY_COLUMNS = [
    "symbol",
    "spread_median_bps",
    "spread_p25_bps",
    "spread_p10_bps",
    "spread_p90_bps",
    "uptime",
    "quoteVolume_24h",
    "quoteVolume_24h_raw",
    "volume_24h_raw",
    "mid_price",
    "quoteVolume_24h_est",
    "quoteVolume_24h_effective",
    "used_quote_volume_estimate",
    "trades_24h",
    "trade_count_missing",
    "edge_mm_bps",
    "edge_mm_p25_bps",
    "edge_mt_bps",
    "net_edge_bps",
    "pass_spread",
    "score",
    "fail_reasons",
]


def _format_optional(value: float | int | None) -> str | float | int:
    if value is None:
        return ""
    return value


def _row_payload(result: ScoreResult) -> dict[str, object]:
    stats = result.spread_stats
    # Determine if quote volume estimate was used: true if we have estimate but not raw
    used_quote_volume_estimate = (
        stats.quote_volume_24h_est is not None
        and stats.quote_volume_24h_raw is None
    )
    # Trade count is missing if it's None
    trade_count_missing = stats.trades_24h is None

    return {
        "symbol": result.symbol,
        "spread_median_bps": stats.spread_median_bps,
        "spread_p25_bps": stats.spread_p25_bps,
        "spread_p10_bps": stats.spread_p10_bps,
        "spread_p90_bps": stats.spread_p90_bps,
        "uptime": stats.uptime,
        "quoteVolume_24h": stats.quote_volume_24h,
        "quoteVolume_24h_raw": stats.quote_volume_24h_raw,
        "volume_24h_raw": stats.volume_24h_raw,
        "mid_price": stats.mid_price,
        "quoteVolume_24h_est": stats.quote_volume_24h_est,
        "quoteVolume_24h_effective": stats.quote_volume_24h_effective,
        "used_quote_volume_estimate": used_quote_volume_estimate,
        "trades_24h": stats.trades_24h,
        "trade_count_missing": trade_count_missing,
        "missing_24h_stats": stats.missing_24h_stats,
        "missing_24h_reason": stats.missing_24h_reason,
        "edge_mm_bps": result.edge_mm_bps,
        "edge_mm_p25_bps": result.edge_mm_p25_bps,
        "edge_mt_bps": result.edge_mt_bps,
        "net_edge_bps": result.net_edge_bps,
        "pass_spread": result.pass_spread,
        "score": result.score,
        "fail_reasons": list(result.fail_reasons),
    }


def export_summary(
    output_dir: Path,
    results: Iterable[ScoreResult],
    *,
    logger: logging.Logger | None = None,
    progress_every: int = 200,
) -> SummaryExportPaths:
    output_dir.mkdir(parents=True, exist_ok=True)

    log = logger or logging.getLogger(__name__)
    results_list = sorted(list(results), key=lambda item: (-item.score, item.symbol))
    csv_path = output_dir / "summary.csv"
    json_path = output_dir / "summary.json"

    current_symbol: str | None = None
    row_idx: int | None = None
    try:
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
            writer.writeheader()
            for row_idx, result in enumerate(results_list, start=1):
                current_symbol = result.symbol
                payload = _row_payload(result)
                payload["fail_reasons"] = ";".join(result.fail_reasons)
                writer.writerow({key: _format_optional(payload[key]) for key in SUMMARY_COLUMNS})
                if progress_every > 0 and row_idx % progress_every == 0:
                    log_event(
                        log,
                        logging.INFO,
                        "export_progress",
                        "Summary export progress",
                        file=csv_path.name,
                        row_idx=row_idx,
                        symbol=current_symbol,
                    )
    except Exception as exc:  # noqa: BLE001
        log_event(
            log,
            logging.ERROR,
            "export_failed",
            "Summary export failed",
            file=csv_path.name,
            row_idx=row_idx,
            symbol=current_symbol,
            exc_info=exc,
        )
        raise

    try:
        json_payload = [_row_payload(result) for result in results_list]
        json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log_event(
            log,
            logging.ERROR,
            "export_failed",
            "Summary JSON export failed",
            file=json_path.name,
            exc_info=exc,
        )
        raise

    return SummaryExportPaths(csv_path=csv_path, json_path=json_path)
