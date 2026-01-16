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
    "trades_24h",
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
        "trades_24h": stats.trades_24h,
        "missing_24h_stats": stats.missing_24h_stats,
        "missing_24h_reason": stats.missing_24h_reason,
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
