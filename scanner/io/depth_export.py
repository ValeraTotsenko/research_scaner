from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from scanner.analytics.scoring import ScoreResult
from scanner.models.depth import DepthSymbolMetrics
from scanner.obs.logging import log_event


@dataclass(frozen=True)
class DepthExportPaths:
    depth_metrics_path: Path
    summary_enriched_path: Path | None


def _band_bid_columns(band_bps: Iterable[int]) -> list[str]:
    return [f"band_bid_notional_median_{band}bps" for band in band_bps]


def _band_ask_columns(band_bps: Iterable[int]) -> list[str]:
    return [f"band_ask_notional_median_{band}bps" for band in band_bps]


def export_depth_metrics(
    output_dir: Path,
    results: Iterable[DepthSymbolMetrics],
    *,
    band_bps: Sequence[int],
    logger: logging.Logger | None = None,
    progress_every: int = 200,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "depth_metrics.csv"

    log = logger or logging.getLogger(__name__)
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
        "best_bid_notional_pass",
        "best_ask_notional_pass",
        "unwind_slippage_pass",
        "band_10bps_notional_pass",
        "topn_notional_pass",
        "pass_depth",
        "depth_fail_reasons",
    ]
    # Insert band columns after topn_ask_notional_median
    columns = (
        columns[:10]
        + _band_bid_columns(band_bps)
        + _band_ask_columns(band_bps)
        + columns[10:]
    )

    current_symbol: str | None = None
    row_idx: int | None = None
    try:
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row_idx, result in enumerate(sorted(results, key=lambda item: item.symbol), start=1):
                current_symbol = result.symbol
                band_bid_payload = result.band_bid_notional_median or {}
                band_ask_payload = result.band_ask_notional_median or {}
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
                    "best_bid_notional_pass": result.best_bid_notional_pass,
                    "best_ask_notional_pass": result.best_ask_notional_pass,
                    "unwind_slippage_pass": result.unwind_slippage_pass,
                    "band_10bps_notional_pass": (
                        "" if result.band_10bps_notional_pass is None else result.band_10bps_notional_pass
                    ),
                    "topn_notional_pass": "" if result.topn_notional_pass is None else result.topn_notional_pass,
                    "pass_depth": result.pass_depth,
                    "depth_fail_reasons": ";".join(result.fail_reasons),
                }
                for band in band_bps:
                    row[f"band_bid_notional_median_{band}bps"] = band_bid_payload.get(band, "")
                    row[f"band_ask_notional_median_{band}bps"] = band_ask_payload.get(band, "")
                writer.writerow(row)
                if progress_every > 0 and row_idx % progress_every == 0:
                    log_event(
                        log,
                        logging.INFO,
                        "export_progress",
                        "Depth metrics export progress",
                        file=csv_path.name,
                        row_idx=row_idx,
                        symbol=current_symbol,
                    )
    except Exception as exc:  # noqa: BLE001
        log_event(
            log,
            logging.ERROR,
            "export_failed",
            "Depth metrics export failed",
            file=csv_path.name,
            row_idx=row_idx,
            symbol=current_symbol,
            exc_info=exc,
        )
        raise

    return csv_path


def export_summary_enriched(
    output_dir: Path,
    summary_results: Sequence[ScoreResult],
    depth_results: Sequence[DepthSymbolMetrics],
    *,
    band_bps: Sequence[int],
    edge_min_bps: float,
    logger: logging.Logger | None = None,
    progress_every: int = 200,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "summary_enriched.csv"

    depth_by_symbol = {item.symbol: item for item in depth_results}
    columns = [
        "symbol",
        "score",
        "pass_spread",
        "pass_depth",
        "best_bid_notional_pass",
        "best_ask_notional_pass",
        "unwind_slippage_pass",
        "band_10bps_notional_pass",
        "topn_notional_pass",
        "pass_total",
        "best_bid_notional_median",
        "best_ask_notional_median",
        "topn_bid_notional_median",
        "topn_ask_notional_median",
        "unwind_slippage_p90_bps",
    ] + _band_bid_columns(band_bps) + _band_ask_columns(band_bps) + [
        "depth_fail_reasons",
    ]

    log = logger or logging.getLogger(__name__)
    current_symbol: str | None = None
    row_idx: int | None = None

    try:
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row_idx, result in enumerate(
                sorted(summary_results, key=lambda item: (-item.score, item.symbol)),
                start=1,
            ):
                current_symbol = result.symbol
                depth = depth_by_symbol.get(result.symbol)
                pass_depth = depth.pass_depth if depth else False
                pass_total = bool(
                    result.pass_spread
                    and pass_depth
                    and result.edge_mm_bps is not None
                    and result.edge_mm_bps >= edge_min_bps
                )
                row = {
                    "symbol": result.symbol,
                    "score": result.score,
                    "pass_spread": result.pass_spread,
                    "pass_depth": pass_depth,
                    "best_bid_notional_pass": depth.best_bid_notional_pass if depth else "",
                    "best_ask_notional_pass": depth.best_ask_notional_pass if depth else "",
                    "unwind_slippage_pass": depth.unwind_slippage_pass if depth else "",
                    "band_10bps_notional_pass": (
                        "" if depth is None or depth.band_10bps_notional_pass is None else depth.band_10bps_notional_pass
                    ),
                    "topn_notional_pass": "" if depth is None or depth.topn_notional_pass is None else depth.topn_notional_pass,
                    "pass_total": pass_total,
                    "best_bid_notional_median": depth.best_bid_notional_median if depth else "",
                    "best_ask_notional_median": depth.best_ask_notional_median if depth else "",
                    "topn_bid_notional_median": depth.topn_bid_notional_median if depth else "",
                    "topn_ask_notional_median": depth.topn_ask_notional_median if depth else "",
                    "unwind_slippage_p90_bps": depth.unwind_slippage_p90_bps if depth else "",
                    "depth_fail_reasons": ";".join(depth.fail_reasons) if depth else "no_depth_data",
                }
                band_bid_payload = (depth.band_bid_notional_median or {}) if depth else {}
                band_ask_payload = (depth.band_ask_notional_median or {}) if depth else {}
                for band in band_bps:
                    row[f"band_bid_notional_median_{band}bps"] = band_bid_payload.get(band, "")
                    row[f"band_ask_notional_median_{band}bps"] = band_ask_payload.get(band, "")
                writer.writerow(row)
                if progress_every > 0 and row_idx % progress_every == 0:
                    log_event(
                        log,
                        logging.INFO,
                        "export_progress",
                        "Summary enriched export progress",
                        file=csv_path.name,
                        row_idx=row_idx,
                        symbol=current_symbol,
                    )
    except Exception as exc:  # noqa: BLE001
        log_event(
            log,
            logging.ERROR,
            "export_failed",
            "Summary enriched export failed",
            file=csv_path.name,
            row_idx=row_idx,
            symbol=current_symbol,
            exc_info=exc,
        )
        raise

    return csv_path
