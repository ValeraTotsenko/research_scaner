from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from scanner.analytics.scoring import ScoreResult
from scanner.models.depth import DepthSymbolMetrics


@dataclass(frozen=True)
class DepthExportPaths:
    depth_metrics_path: Path
    summary_enriched_path: Path | None


def _band_columns(band_bps: Iterable[int]) -> list[str]:
    return [f"band_bid_notional_median_{band}bps" for band in band_bps]


def export_depth_metrics(
    output_dir: Path,
    results: Iterable[DepthSymbolMetrics],
    *,
    band_bps: Sequence[int],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "depth_metrics.csv"

    columns = [
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
        "pass_depth",
        "fail_reasons",
    ]
    columns = columns[:10] + _band_columns(band_bps) + columns[10:]

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for result in sorted(results, key=lambda item: item.symbol):
            band_payload = result.band_bid_notional_median or {}
            row = {
                "symbol": result.symbol,
                "sample_count": result.sample_count,
                "valid_samples": result.valid_samples,
                "empty_book_count": result.empty_book_count,
                "invalid_book_count": result.invalid_book_count,
                "symbol_unavailable_count": result.symbol_unavailable_count,
                "best_bid_notional_median": result.best_bid_notional_median or "",
                "best_ask_notional_median": result.best_ask_notional_median or "",
                "topn_bid_notional_median": result.topn_bid_notional_median or "",
                "topn_ask_notional_median": result.topn_ask_notional_median or "",
                "unwind_slippage_p90_bps": result.unwind_slippage_p90_bps or "",
                "uptime": result.uptime,
                "pass_depth": result.pass_depth,
                "fail_reasons": ";".join(result.fail_reasons),
            }
            for band in band_bps:
                row[f"band_bid_notional_median_{band}bps"] = band_payload.get(band, "")
            writer.writerow(row)

    return csv_path


def export_summary_enriched(
    output_dir: Path,
    summary_results: Sequence[ScoreResult],
    depth_results: Sequence[DepthSymbolMetrics],
    *,
    band_bps: Sequence[int],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "summary_enriched.csv"

    depth_by_symbol = {item.symbol: item for item in depth_results}
    columns = [
        "symbol",
        "score",
        "pass_spread",
        "pass_depth",
        "pass_total",
        "best_bid_notional_median",
        "best_ask_notional_median",
        "topn_bid_notional_median",
        "topn_ask_notional_median",
        "unwind_slippage_p90_bps",
    ] + _band_columns(band_bps) + [
        "depth_fail_reasons",
    ]

    logger = logging.getLogger(__name__)
    current_symbol: str | None = None

    try:
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for result in sorted(summary_results, key=lambda item: (-item.score, item.symbol)):
                current_symbol = result.symbol
                depth = depth_by_symbol.get(result.symbol)
                pass_depth = depth.pass_depth if depth else False
                pass_total = result.pass_spread and pass_depth
                row = {
                    "symbol": result.symbol,
                    "score": result.score,
                    "pass_spread": result.pass_spread,
                    "pass_depth": pass_depth,
                    "pass_total": pass_total,
                    "best_bid_notional_median": depth.best_bid_notional_median if depth else "",
                    "best_ask_notional_median": depth.best_ask_notional_median if depth else "",
                    "topn_bid_notional_median": depth.topn_bid_notional_median if depth else "",
                    "topn_ask_notional_median": depth.topn_ask_notional_median if depth else "",
                    "unwind_slippage_p90_bps": depth.unwind_slippage_p90_bps if depth else "",
                    "depth_fail_reasons": ";".join(depth.fail_reasons) if depth else "no_depth_data",
                }
                band_payload = (depth.band_bid_notional_median or {}) if depth else {}
                for band in band_bps:
                    row[f"band_bid_notional_median_{band}bps"] = band_payload.get(band, "")
                writer.writerow(row)
    except Exception:
        logger.error(
            "Summary enriched export failed",
            exc_info=True,
            extra={
                "event": "export_failed",
                "extra": {
                    "stage": "depth",
                    "file": csv_path.name,
                    "symbol": current_symbol,
                },
            },
        )
        raise

    return csv_path
