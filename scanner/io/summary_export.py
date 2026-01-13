from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scanner.analytics.scoring import ScoreResult


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
        "trades_24h": stats.trades_24h,
        "net_edge_bps": result.net_edge_bps,
        "pass_spread": result.pass_spread,
        "score": result.score,
        "fail_reasons": list(result.fail_reasons),
    }


def export_summary(output_dir: Path, results: Iterable[ScoreResult]) -> SummaryExportPaths:
    output_dir.mkdir(parents=True, exist_ok=True)

    results_list = sorted(list(results), key=lambda item: (-item.score, item.symbol))
    csv_path = output_dir / "summary.csv"
    json_path = output_dir / "summary.json"

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for result in results_list:
            payload = _row_payload(result)
            payload["fail_reasons"] = ";".join(result.fail_reasons)
            writer.writerow({key: _format_optional(payload[key]) for key in SUMMARY_COLUMNS})

    json_payload = [_row_payload(result) for result in results_list]
    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return SummaryExportPaths(csv_path=csv_path, json_path=json_path)
