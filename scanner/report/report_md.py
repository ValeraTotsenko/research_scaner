from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from scanner.config import AppConfig
from scanner.io.summary_export import SUMMARY_COLUMNS
from scanner.obs.logging import log_event
from scanner.obs.metrics import summarize_api_health, update_metrics


@dataclass(frozen=True)
class SummaryRow:
    symbol: str
    spread_median_bps: float | None
    spread_p10_bps: float | None
    spread_p25_bps: float | None
    spread_p90_bps: float | None
    uptime: float | None
    quote_volume_24h: float | None
    quote_volume_24h_raw: float | None
    volume_24h_raw: float | None
    mid_price: float | None
    quote_volume_24h_est: float | None
    quote_volume_24h_effective: float | None
    trades_24h: int | None
    edge_mm_bps: float | None
    edge_with_unwind_bps: float | None
    net_edge_bps: float | None
    pass_spread: bool
    score: float
    fail_reasons: tuple[str, ...]


@dataclass(frozen=True)
class SummaryEnrichedRow:
    symbol: str
    score: float
    pass_spread: bool
    pass_depth: bool | None
    pass_total: bool
    depth_fail_reasons: tuple[str, ...]


@dataclass(frozen=True)
class DepthRow:
    symbol: str
    pass_depth: bool
    uptime: float | None
    depth_fail_reasons: tuple[str, ...]


SUMMARY_REQUIRED_COLUMNS = set(SUMMARY_COLUMNS)
SUMMARY_ENRICHED_REQUIRED_COLUMNS = {
    "symbol",
    "score",
    "pass_spread",
    "pass_depth",
    "pass_total",
    "depth_fail_reasons",
}
DEPTH_REQUIRED_COLUMNS = {"symbol", "pass_depth", "uptime", "depth_fail_reasons"}


def _parse_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def _split_reasons(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    return tuple(part for part in str(value).split(";") if part)


def _read_summary(path: Path) -> list[SummaryRow]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        if not SUMMARY_REQUIRED_COLUMNS.issubset(fieldnames):
            missing = SUMMARY_REQUIRED_COLUMNS - fieldnames
            raise ValueError(
                "Incompatible summary format (missing columns: "
                f"{sorted(missing)}). Rerun with v0.1+"
            )

        rows: list[SummaryRow] = []
        for row in reader:
            rows.append(
                SummaryRow(
                    symbol=str(row.get("symbol", "")),
                    spread_median_bps=_parse_float(row.get("spread_median_bps")),
                    spread_p10_bps=_parse_float(row.get("spread_p10_bps")),
                    spread_p25_bps=_parse_float(row.get("spread_p25_bps")),
                    spread_p90_bps=_parse_float(row.get("spread_p90_bps")),
                    uptime=_parse_float(row.get("uptime")),
                    quote_volume_24h=_parse_float(row.get("quoteVolume_24h")),
                    quote_volume_24h_raw=_parse_float(row.get("quoteVolume_24h_raw")),
                    volume_24h_raw=_parse_float(row.get("volume_24h_raw")),
                    mid_price=_parse_float(row.get("mid_price")),
                    quote_volume_24h_est=_parse_float(row.get("quoteVolume_24h_est")),
                    quote_volume_24h_effective=_parse_float(row.get("quoteVolume_24h_effective")),
                    trades_24h=_parse_int(row.get("trades_24h")),
                    edge_mm_bps=_parse_float(row.get("edge_mm_bps")),
                    edge_with_unwind_bps=_parse_float(row.get("edge_with_unwind_bps")),
                    net_edge_bps=_parse_float(row.get("net_edge_bps")),
                    pass_spread=_parse_bool(row.get("pass_spread")),
                    score=float(row.get("score") or 0),
                    fail_reasons=_split_reasons(row.get("fail_reasons")),
                )
            )
    return rows


def _read_summary_enriched(path: Path) -> list[SummaryEnrichedRow]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        if not SUMMARY_ENRICHED_REQUIRED_COLUMNS.issubset(fieldnames):
            missing = SUMMARY_ENRICHED_REQUIRED_COLUMNS - fieldnames
            raise ValueError(
                "Incompatible summary_enriched format (missing columns: "
                f"{sorted(missing)}). Rerun with v0.1+"
            )

        rows: list[SummaryEnrichedRow] = []
        for row in reader:
            rows.append(
                SummaryEnrichedRow(
                    symbol=str(row.get("symbol", "")),
                    score=float(row.get("score") or 0),
                    pass_spread=_parse_bool(row.get("pass_spread")),
                    pass_depth=_parse_bool(row.get("pass_depth")),
                    pass_total=_parse_bool(row.get("pass_total")),
                    depth_fail_reasons=_split_reasons(row.get("depth_fail_reasons")),
                )
            )
    return rows


def _read_depth_metrics(path: Path) -> list[DepthRow]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        if not DEPTH_REQUIRED_COLUMNS.issubset(fieldnames):
            missing = DEPTH_REQUIRED_COLUMNS - fieldnames
            raise ValueError(
                "Incompatible depth_metrics format (missing columns: "
                f"{sorted(missing)}). Rerun with v0.1+"
            )

        rows: list[DepthRow] = []
        for row in reader:
            rows.append(
                DepthRow(
                    symbol=str(row.get("symbol", "")),
                    pass_depth=_parse_bool(row.get("pass_depth")),
                    uptime=_parse_float(row.get("uptime")),
                    depth_fail_reasons=_split_reasons(row.get("depth_fail_reasons")),
                )
            )
    return rows


def _quantiles(values: Iterable[float], probs: Iterable[float]) -> dict[float, float | None]:
    data = sorted(values)
    if not data:
        return {p: None for p in probs}

    results: dict[float, float | None] = {}
    last_index = len(data) - 1
    for prob in probs:
        position = prob * last_index
        lower_idx = int(position)
        upper_idx = min(lower_idx + 1, last_index)
        fraction = position - lower_idx
        lower = data[lower_idx]
        upper = data[upper_idx]
        results[prob] = lower + (upper - lower) * fraction
    return results


def _format_value(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _render_report(
    *,
    run_meta: dict[str, object],
    metrics_payload: dict[str, object] | None,
    cfg: AppConfig,
    summary_rows: list[SummaryRow],
    summary_enriched: list[SummaryEnrichedRow] | None,
    depth_rows: list[DepthRow] | None,
    shortlist_rows: list[SummaryEnrichedRow],
) -> str:
    lines: list[str] = ["# Report", ""]

    run_id = run_meta.get("run_id", "")
    started_at = run_meta.get("started_at", "")
    git_commit = run_meta.get("git_commit", "")
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    lines += [
        "## Run meta",
        "",
        f"- Run ID: {run_id}",
        f"- Started at: {started_at}",
        f"- Report generated at: {generated_at}",
        f"- Git commit: {git_commit}",
    ]
    lines.append("")
    lines.append("### Parameters")
    lines.append("")
    lines.extend(
        [
            f"- Spread sampling: duration_s={cfg.sampling.spread.duration_s}, interval_s={cfg.sampling.spread.interval_s}, min_uptime={cfg.sampling.spread.min_uptime}",
            f"- Depth sampling: duration_s={cfg.sampling.depth.duration_s}, interval_s={cfg.sampling.depth.interval_s}, limit={cfg.sampling.depth.limit}",
            (
                "- Spread thresholds: "
                f"median_min_bps={cfg.thresholds.spread.median_min_bps}, "
                f"median_max_bps={cfg.thresholds.spread.median_max_bps}, "
                f"p90_min_bps={cfg.thresholds.spread.p90_min_bps}, "
                f"p90_max_bps={cfg.thresholds.spread.p90_max_bps}"
            ),
            (
                "- Fees: "
                f"maker_bps={cfg.fees.maker_bps}, "
                f"taker_bps={cfg.fees.taker_bps}"
            ),
            (
                "- Edge thresholds: "
                f"edge_min_bps={cfg.thresholds.edge_min_bps}, "
                f"slippage_buffer_bps={cfg.thresholds.slippage_buffer_bps} "
                f"(edge_mm = spread - 2*maker - buffer)"
            ),
            (
                "- Depth thresholds: "
                f"best_level_min_notional={cfg.thresholds.depth.best_level_min_notional}, "
                f"unwind_slippage_max_bps={cfg.thresholds.depth.unwind_slippage_max_bps}, "
                f"band_10bps_min_notional={cfg.thresholds.depth.band_10bps_min_notional}, "
                f"topN_min_notional={cfg.thresholds.depth.topN_min_notional}"
            ),
            (
                "- Depth optional checks: "
                f"enable_band_checks={cfg.depth.enable_band_checks}, "
                f"enable_topN_checks={cfg.depth.enable_topN_checks}"
            ),
            f"- Report shortlist size: top_n={cfg.report.top_n}",
        ]
    )

    lines.append("")
    lines.append("## API health summary")
    lines.append("")
    api_health = summarize_api_health(metrics_payload or {})
    run_health = run_meta.get("run_health", api_health.get("run_health", "n/a"))
    lines.append(f"- Run health: {run_health}")
    if metrics_payload:
        lines.extend(
            [
                f"- HTTP 429 total: {api_health['http_429_total']}",
                f"- HTTP 403 total: {api_health['http_403_total']}",
                f"- HTTP 5xx total: {api_health['http_5xx_total']}",
            ]
        )
    else:
        lines.append("- HTTP metrics unavailable.")

    lines.append("")
    lines.append("## Universe stats")
    lines.append("")
    total_symbols = len(summary_rows)
    pass_spread_count = sum(1 for row in summary_rows if row.pass_spread)
    if summary_enriched is None:
        pass_total_count = str(
            sum(
                1
                for row in summary_rows
                if row.pass_spread
                and row.edge_mm_bps is not None
                and row.edge_mm_bps >= cfg.thresholds.edge_min_bps
            )
        )
    else:
        pass_total_count = str(sum(1 for row in summary_enriched if row.pass_total))

    lines.extend(
        [
            f"- Symbols scanned: {total_symbols}",
            f"- PASS_SPREAD: {pass_spread_count}",
            f"- PASS_TOTAL: {pass_total_count}",
        ]
    )

    lines.append("")
    lines.append("## Spread stats quantiles")
    lines.append("")
    spread_medians = [row.spread_median_bps for row in summary_rows if row.spread_median_bps is not None]
    spread_p90s = [row.spread_p90_bps for row in summary_rows if row.spread_p90_bps is not None]
    probs = [0.1, 0.25, 0.5, 0.75, 0.9]
    median_quantiles = _quantiles(spread_medians, probs)
    p90_quantiles = _quantiles(spread_p90s, probs)

    quantile_rows = []
    for prob in probs:
        quantile_rows.append(
            [
                f"p{int(prob * 100)}",
                _format_value(median_quantiles[prob]),
                _format_value(p90_quantiles[prob]),
            ]
        )

    lines.extend(
        _markdown_table(
            ["Quantile", "spread_median_bps", "spread_p90_bps"],
            quantile_rows,
        )
    )

    lines.append("")
    lines.append("## Depth check results")
    lines.append("")
    if summary_enriched is None:
        lines.append("- Depth stage: no depth stage (summary_enriched.csv missing)")
    else:
        # Use depth_rows count for actual depth-checked symbols.
        # summary_enriched includes ALL symbols (some may have no depth data).
        # depth_rows contains only symbols that were actually checked for depth.
        if depth_rows:
            depth_checked_count = len(depth_rows)
            pass_depth_count = sum(1 for row in depth_rows if row.pass_depth)
            uptimes = [row.uptime for row in depth_rows if row.uptime is not None]
            lines.extend(
                [
                    f"- Depth candidates checked: {depth_checked_count}",
                    f"- PASS_DEPTH: {pass_depth_count}",
                ]
            )
            if uptimes:
                lines.append(f"- Depth uptime p50: {_format_value(_quantiles(uptimes, [0.5])[0.5])}")
        else:
            # Fallback to summary_enriched if depth_rows unavailable
            depth_total = len(summary_enriched)
            pass_depth_count = sum(1 for row in summary_enriched if row.pass_depth)
            lines.extend(
                [
                    f"- Depth symbols (from enriched): {depth_total}",
                    f"- PASS_DEPTH: {pass_depth_count}",
                ]
            )

    lines.append("")
    lines.append("## Top candidates")
    lines.append("")
    if shortlist_rows:
        rows = []
        for row in shortlist_rows:
            rows.append(
                [
                    row.symbol,
                    f"{row.score:.2f}",
                    "yes" if row.pass_spread else "no",
                    "yes" if row.pass_depth else "no" if summary_enriched is not None else "n/a",
                    "yes" if row.pass_total else "no",
                ]
            )
        lines.extend(
            _markdown_table(
                ["symbol", "score", "pass_spread", "pass_depth", "pass_total"],
                rows,
            )
        )
    else:
        lines.append("No candidates qualified for the shortlist.")

    lines.append("")
    lines.append("## Fail reason breakdown")
    lines.append("")

    spread_reasons: dict[str, int] = {}
    for row in summary_rows:
        for reason in row.fail_reasons:
            spread_reasons[reason] = spread_reasons.get(reason, 0) + 1

    if spread_reasons:
        lines.append("### Spread stage")
        lines.append("")
        rows = [[reason, str(count)] for reason, count in sorted(spread_reasons.items())]
        lines.extend(_markdown_table(["reason", "count"], rows))
    else:
        lines.append("- No spread failures recorded.")

    lines.append("")

    depth_reasons: dict[str, int] = {}
    if summary_enriched is None:
        lines.append("- Depth stage not executed.")
    else:
        if depth_rows:
            for row in depth_rows:
                for reason in row.depth_fail_reasons:
                    depth_reasons[reason] = depth_reasons.get(reason, 0) + 1
        else:
            for row in summary_enriched:
                for reason in row.depth_fail_reasons:
                    depth_reasons[reason] = depth_reasons.get(reason, 0) + 1

        if depth_reasons:
            lines.append("### Depth stage")
            lines.append("")
            rows = [[reason, str(count)] for reason, count in sorted(depth_reasons.items())]
            lines.extend(_markdown_table(["reason", "count"], rows))
        else:
            lines.append("- No depth failures recorded.")

    if not shortlist_rows:
        lines.append("")
        lines.append(
            "Shortlist is empty. Common reasons are strict spread/depth thresholds or low uptime. "
            "See the breakdown above for details."
        )

    lines.append("")
    return "\n".join(lines)


def _build_shortlist(
    *,
    summary_rows: list[SummaryRow],
    summary_enriched: list[SummaryEnrichedRow] | None,
    top_n: int,
    edge_min_bps: float,
) -> list[SummaryEnrichedRow]:
    rows: list[SummaryEnrichedRow]
    if summary_enriched is None:
        rows = [
            SummaryEnrichedRow(
                symbol=row.symbol,
                score=row.score,
                pass_spread=row.pass_spread,
                pass_depth=None,
                pass_total=bool(
                    row.pass_spread
                    and row.edge_mm_bps is not None
                    and row.edge_mm_bps >= edge_min_bps
                ),
                depth_fail_reasons=(),
            )
            for row in summary_rows
        ]
    else:
        rows = list(summary_enriched)

    rows_sorted = sorted(
        rows,
        key=lambda item: (
            0 if item.pass_total else 1,
            -item.score,
            item.symbol,
        ),
    )
    return rows_sorted[: max(0, top_n)]


def _write_shortlist(path: Path, rows: list[SummaryEnrichedRow]) -> None:
    columns = ["symbol", "score", "pass_spread", "pass_depth", "pass_total"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "symbol": row.symbol,
                    "score": f"{row.score:.6f}",
                    "pass_spread": row.pass_spread,
                    "pass_depth": "" if row.pass_depth is None else row.pass_depth,
                    "pass_total": row.pass_total,
                }
            )


def generate_report(run_dir: Path, cfg: AppConfig) -> None:
    summary_path = run_dir / "summary.csv"
    if not summary_path.exists():
        raise FileNotFoundError(f"summary.csv not found in {run_dir}")

    run_meta_path = run_dir / "run_meta.json"
    if not run_meta_path.exists():
        raise FileNotFoundError(f"run_meta.json not found in {run_dir}")

    run_meta = json.loads(run_meta_path.read_text(encoding="utf-8"))
    summary_rows = _read_summary(summary_path)

    summary_enriched_path = run_dir / "summary_enriched.csv"
    summary_enriched = _read_summary_enriched(summary_enriched_path) if summary_enriched_path.exists() else None

    depth_metrics_path = run_dir / "depth_metrics.csv"
    depth_rows = _read_depth_metrics(depth_metrics_path) if depth_metrics_path.exists() else None

    shortlist_rows = _build_shortlist(
        summary_rows=summary_rows,
        summary_enriched=summary_enriched,
        top_n=cfg.report.top_n,
        edge_min_bps=cfg.thresholds.edge_min_bps,
    )
    shortlist_path = run_dir / "shortlist.csv"
    _write_shortlist(shortlist_path, shortlist_rows)

    metrics_payload: dict[str, object] | None = None
    metrics_path = run_dir / "metrics.json"
    if metrics_path.exists():
        raw_metrics = metrics_path.read_text(encoding="utf-8").strip()
        if raw_metrics:
            metrics_payload = json.loads(raw_metrics)

    report_path = run_dir / "report.md"
    report_path.write_text(
        _render_report(
            run_meta=run_meta,
            metrics_payload=metrics_payload,
            cfg=cfg,
            summary_rows=summary_rows,
            summary_enriched=summary_enriched,
            depth_rows=depth_rows,
            shortlist_rows=shortlist_rows,
        ),
        encoding="utf-8",
    )

    update_metrics(
        metrics_path,
        increments={"report_generated_total": 1},
        gauges={"shortlist_size": len(shortlist_rows)},
    )

    logger = logging.getLogger(__name__)
    log_event(
        logger,
        logging.INFO,
        "report_generated",
        "Report generated",
        shortlist_count=len(shortlist_rows),
        path=str(report_path),
    )
