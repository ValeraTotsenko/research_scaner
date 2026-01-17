"""
New report generator according to TZ_updates.md specification.

This module generates comprehensive markdown reports with the following 7+1 sections:
1. Run meta
2. Parameters
3. Universe stats
4. Spread stats
5. Depth results
6. Top candidates table
7. Fail reason breakdown
8. Notes (warnings/caveats)
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from scanner.config import AppConfig
from scanner.obs.logging import log_event
from scanner.obs.metrics import summarize_api_health, update_metrics


@dataclass(frozen=True)
class SummaryRow:
    """Row from summary.csv."""
    symbol: str
    spread_median_bps: float | None
    spread_p10_bps: float | None
    spread_p25_bps: float | None
    spread_p90_bps: float | None
    uptime: float | None
    quote_volume_24h_effective: float | None
    trades_24h: int | None
    edge_mm_bps: float | None
    edge_mm_p25_bps: float | None
    edge_mt_bps: float | None
    pass_spread: bool
    score: float
    fail_reasons: tuple[str, ...]


@dataclass(frozen=True)
class DepthRow:
    """Row from depth_metrics.csv."""
    symbol: str
    pass_depth: bool
    uptime: float | None
    best_bid_notional_median: float | None
    best_ask_notional_median: float | None
    unwind_slippage_p90_bps: float | None
    fail_reasons: tuple[str, ...]


@dataclass(frozen=True)
class SummaryEnrichedRow:
    """Row from summary_enriched.csv."""
    symbol: str
    score: float
    pass_spread: bool
    pass_depth: bool | None
    pass_total: bool


def _parse_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
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
    """Read summary.csv and parse into SummaryRow objects."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
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
                    quote_volume_24h_effective=_parse_float(row.get("quoteVolume_24h_effective")),
                    trades_24h=int(float(row["trades_24h"])) if row.get("trades_24h") not in (None, "") else None,
                    edge_mm_bps=_parse_float(row.get("edge_mm_bps")),
                    edge_mm_p25_bps=_parse_float(row.get("edge_mm_p25_bps")),
                    edge_mt_bps=_parse_float(row.get("edge_mt_bps")),
                    pass_spread=_parse_bool(row.get("pass_spread")),
                    score=float(row.get("score") or 0),
                    fail_reasons=_split_reasons(row.get("fail_reasons")),
                )
            )
    return rows


def _read_depth_metrics(path: Path) -> list[DepthRow]:
    """Read depth_metrics.csv and parse into DepthRow objects."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[DepthRow] = []
        for row in reader:
            rows.append(
                DepthRow(
                    symbol=str(row.get("symbol", "")),
                    pass_depth=_parse_bool(row.get("pass_depth")),
                    uptime=_parse_float(row.get("uptime")),
                    best_bid_notional_median=_parse_float(row.get("best_bid_notional_median")),
                    best_ask_notional_median=_parse_float(row.get("best_ask_notional_median")),
                    unwind_slippage_p90_bps=_parse_float(row.get("unwind_slippage_p90_bps")),
                    fail_reasons=_split_reasons(row.get("depth_fail_reasons")),
                )
            )
    return rows


def _read_summary_enriched(path: Path) -> list[SummaryEnrichedRow]:
    """Read summary_enriched.csv and parse into SummaryEnrichedRow objects."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[SummaryEnrichedRow] = []
        for row in reader:
            rows.append(
                SummaryEnrichedRow(
                    symbol=str(row.get("symbol", "")),
                    score=float(row.get("score") or 0),
                    pass_spread=_parse_bool(row.get("pass_spread")),
                    pass_depth=_parse_bool(row.get("pass_depth")) if row.get("pass_depth") not in (None, "") else None,
                    pass_total=_parse_bool(row.get("pass_total")),
                )
            )
    return rows


def _quantiles(values: Iterable[float], probs: Iterable[float]) -> dict[float, float | None]:
    """Calculate percentiles using linear interpolation."""
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


def _format_value(value: float | None, decimals: int = 2) -> str:
    """Format numeric value with specified decimals, or 'n/a' if None."""
    if value is None:
        return "n/a"
    return f"{value:.{decimals}f}"


def _markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Generate markdown table from headers and rows."""
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _render_report(
    *,
    run_meta: dict[str, object],
    pipeline_state: dict[str, object] | None,
    metrics_payload: dict[str, object] | None,
    cfg: AppConfig,
    summary_rows: list[SummaryRow],
    depth_rows: list[DepthRow] | None,
    summary_enriched: list[SummaryEnrichedRow] | None,
) -> str:
    """
    Render markdown report according to TZ_updates.md specification.

    Generates 7+1 sections:
    1. Run meta
    2. Parameters
    3. Universe stats
    4. Spread stats
    5. Depth results
    6. Top candidates table
    7. Fail reason breakdown
    8. Notes
    """
    lines: list[str] = ["# MEXC Spread Feasibility Scanner Report", ""]

    # ========== Section 1: Run Meta ==========
    run_id = run_meta.get("run_id", "unknown")
    started_at = run_meta.get("started_at", "unknown")
    git_commit = run_meta.get("git_commit", "unknown")
    scanner_version = "v0.1.0"  # Can be extracted from package if needed
    report_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    lines += [
        "## 1. Run Meta",
        "",
        f"- **Run ID**: `{run_id}`",
        f"- **Started at**: {started_at}",
        f"- **Report generated at**: {report_at}",
        f"- **Scanner version**: {scanner_version}",
        f"- **Git commit**: `{git_commit}`",
        "",
    ]

    # ========== Section 2: Parameters ==========
    lines += [
        "## 2. Parameters",
        "",
        "### Spread Sampling",
        f"- duration_s: {cfg.sampling.spread.duration_s}",
        f"- interval_s: {cfg.sampling.spread.interval_s}",
        f"- min_uptime: {cfg.sampling.spread.min_uptime}",
        "",
        "### Depth Sampling",
        f"- duration_s: {cfg.sampling.depth.duration_s}",
        f"- interval_s: {cfg.sampling.depth.interval_s}",
        f"- limit: {cfg.sampling.depth.limit}",
        f"- candidates_limit: {cfg.sampling.depth.candidates_limit}",
        "",
        "### Spread Thresholds",
        f"- median_min_bps: {cfg.thresholds.spread.median_min_bps}",
        f"- median_max_bps: {cfg.thresholds.spread.median_max_bps}",
        f"- p90_min_bps: {cfg.thresholds.spread.p90_min_bps}",
        f"- p90_max_bps: {cfg.thresholds.spread.p90_max_bps}",
        "",
        "### Depth Thresholds",
        f"- best_level_min_notional: {cfg.thresholds.depth.best_level_min_notional}",
        f"- unwind_slippage_max_bps: {cfg.thresholds.depth.unwind_slippage_max_bps}",
        "",
        "### Fees & Buffer",
        f"- maker_bps: {cfg.fees.maker_bps}",
        f"- taker_bps: {cfg.fees.taker_bps}",
        f"- buffer_bps: {cfg.thresholds.buffer_bps}",
        f"- Formula: edge_mm_bps = spread_median_bps - 2×maker_bps - buffer_bps",
        "",
    ]

    # ========== Section 3: Universe Stats ==========
    total_symbols = len(summary_rows)
    pass_spread_count = sum(1 for row in summary_rows if row.pass_spread)
    fail_spread_count = total_symbols - pass_spread_count

    lines += [
        "## 3. Universe Stats",
        "",
        f"- **Symbols scanned**: {total_symbols}",
        f"- **PASS_SPREAD**: {pass_spread_count}",
        f"- **FAIL_SPREAD**: {fail_spread_count}",
        "",
    ]

    # ========== Section 4: Spread Stats ==========
    spread_medians = [row.spread_median_bps for row in summary_rows if row.pass_spread and row.spread_median_bps is not None]
    spread_p90s = [row.spread_p90_bps for row in summary_rows if row.pass_spread and row.spread_p90_bps is not None]
    probs = [0.1, 0.25, 0.5, 0.75, 0.9]
    median_quantiles = _quantiles(spread_medians, probs)
    p90_quantiles = _quantiles(spread_p90s, probs)

    lines += [
        "## 4. Spread Stats (PASS_SPREAD symbols only)",
        "",
        "Quantiles of spread_median_bps and spread_p90_bps:",
        "",
    ]

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

    # ========== Section 5: Depth Results ==========
    depth_candidates_requested = cfg.sampling.depth.candidates_limit
    depth_candidates_actual = len(depth_rows) if depth_rows else 0
    pass_depth_count = sum(1 for row in depth_rows if row.pass_depth) if depth_rows else 0
    pass_total_count = sum(1 for row in summary_enriched if row.pass_total) if summary_enriched else 0

    # Get depth stage status from pipeline_state if available
    depth_status = "success"
    depth_timed_out = False
    depth_elapsed_s = None
    if pipeline_state:
        stages = pipeline_state.get("stages", {})
        if isinstance(stages, dict):
            depth_stage = stages.get("depth", {})
            if isinstance(depth_stage, dict):
                depth_status = depth_stage.get("status", "success")
                depth_timed_out = depth_stage.get("timed_out", False)
                depth_elapsed_s = depth_stage.get("elapsed_s")

    lines += [
        "## 5. Depth Results",
        "",
        f"- **Depth candidates requested**: {depth_candidates_requested}",
        f"- **Depth candidates actual**: {depth_candidates_actual}",
        f"- **Stage status**: {depth_status}",
        f"- **Timed out**: {'yes' if depth_timed_out else 'no'}",
    ]

    if depth_elapsed_s is not None:
        lines.append(f"- **Elapsed time**: {depth_elapsed_s:.1f}s")

    lines += [
        f"- **PASS_DEPTH**: {pass_depth_count}/{depth_candidates_actual}",
        f"- **PASS_TOTAL**: {pass_total_count}",
        "",
    ]

    # Depth uptime p50 (only if meaningful)
    if depth_rows:
        uptimes = [row.uptime for row in depth_rows if row.uptime is not None]
        if uptimes:
            uptime_p50 = _quantiles(uptimes, [0.5])[0.5]
            lines.append(f"- **Depth uptime P50**: {_format_value(uptime_p50)}")
        lines.append("")

    # ========== Section 6: Top Candidates Table ==========
    # Build top N candidates sorted by score (pass_total first, then by score descending)
    top_n = cfg.report.top_n
    candidates = []

    if summary_enriched:
        # Use enriched data with depth info
        for enriched in summary_enriched:
            summary = next((s for s in summary_rows if s.symbol == enriched.symbol), None)
            depth = next((d for d in (depth_rows or []) if d.symbol == enriched.symbol), None)

            if summary:
                candidates.append({
                    "symbol": enriched.symbol,
                    "score": enriched.score,
                    "pass_spread": enriched.pass_spread,
                    "pass_depth": enriched.pass_depth,
                    "pass_total": enriched.pass_total,
                    "spread_median_bps": summary.spread_median_bps,
                    "spread_p90_bps": summary.spread_p90_bps,
                    "edge_mm_p25_bps": summary.edge_mm_p25_bps,
                    "edge_mm_bps": summary.edge_mm_bps,
                    "best_bid_notional": depth.best_bid_notional_median if depth else None,
                    "best_ask_notional": depth.best_ask_notional_median if depth else None,
                    "unwind_slippage_p90": depth.unwind_slippage_p90_bps if depth else None,
                    "fail_reasons": list(summary.fail_reasons) + (list(depth.fail_reasons) if depth else []),
                })
    else:
        # Fallback: use summary only
        for summary in summary_rows:
            candidates.append({
                "symbol": summary.symbol,
                "score": summary.score,
                "pass_spread": summary.pass_spread,
                "pass_depth": None,
                "pass_total": summary.pass_spread and (summary.edge_mm_bps or 0) >= cfg.thresholds.edge_min_bps,
                "spread_median_bps": summary.spread_median_bps,
                "spread_p90_bps": summary.spread_p90_bps,
                "edge_mm_p25_bps": summary.edge_mm_p25_bps,
                "edge_mm_bps": summary.edge_mm_bps,
                "best_bid_notional": None,
                "best_ask_notional": None,
                "unwind_slippage_p90": None,
                "fail_reasons": list(summary.fail_reasons),
            })

    # Sort: pass_total first, then by score descending
    candidates_sorted = sorted(
        candidates,
        key=lambda c: (0 if c["pass_total"] else 1, -c["score"], c["symbol"]),
    )[:top_n]

    lines += [
        f"## 6. Top {top_n} Candidates",
        "",
    ]

    if candidates_sorted:
        table_rows = []
        for c in candidates_sorted:
            fail_reasons_str = "; ".join(c["fail_reasons"][:3]) if c["fail_reasons"] else "-"
            if len(c["fail_reasons"]) > 3:
                fail_reasons_str += "..."

            table_rows.append([
                c["symbol"],
                f"{c['score']:.1f}",
                _format_value(c["spread_median_bps"]),
                _format_value(c["spread_p90_bps"]),
                _format_value(c["edge_mm_p25_bps"]),
                _format_value(c["edge_mm_bps"]),
                _format_value(c["best_bid_notional"], 0),
                _format_value(c["best_ask_notional"], 0),
                _format_value(c["unwind_slippage_p90"]),
                "✓" if c["pass_total"] else "✗",
                fail_reasons_str,
            ])

        lines.extend(
            _markdown_table(
                [
                    "Symbol",
                    "Score",
                    "Spread Med",
                    "Spread P90",
                    "Edge P25",
                    "Edge MM",
                    "Bid Liq",
                    "Ask Liq",
                    "Slip P90",
                    "PASS",
                    "Fail Reasons",
                ],
                table_rows,
            )
        )
    else:
        lines.append("*No candidates available.*")

    lines.append("")

    # ========== Section 7: Fail Reason Breakdown ==========
    lines += [
        "## 7. Fail Reason Breakdown",
        "",
        "### Spread Stage",
        "",
    ]

    # Count spread fail reasons (excluding "missing_24h_stats" per TZ)
    spread_reasons: dict[str, int] = {}
    for row in summary_rows:
        for reason in row.fail_reasons:
            if reason != "missing_24h_stats":  # Exclude per TZ requirement
                spread_reasons[reason] = spread_reasons.get(reason, 0) + 1

    if spread_reasons:
        reason_rows = [[reason, str(count)] for reason, count in sorted(spread_reasons.items(), key=lambda x: -x[1])]
        lines.extend(_markdown_table(["Reason", "Count"], reason_rows))
    else:
        lines.append("*No spread failures recorded.*")

    lines.append("")
    lines += ["### Depth Stage", ""]

    # Count depth fail reasons (only from candidates that were checked)
    depth_reasons: dict[str, int] = {}
    if depth_rows:
        for row in depth_rows:
            for reason in row.fail_reasons:
                depth_reasons[reason] = depth_reasons.get(reason, 0) + 1

    if depth_reasons:
        reason_rows = [[reason, str(count)] for reason, count in sorted(depth_reasons.items(), key=lambda x: -x[1])]
        lines.extend(_markdown_table(["Reason", "Count"], reason_rows))
    else:
        lines.append("*No depth failures recorded (or depth stage not executed).*")

    lines.append("")

    # ========== Section 8: Notes ==========
    lines += ["## 8. Notes", ""]

    # Warning if depth stage timed out
    if depth_timed_out:
        lines.append(
            "⚠️ **WARNING**: Depth stage timed out before completion. "
            "Results may be partial. Consider increasing `pipeline.stage_timeouts_s.depth`."
        )
        lines.append("")

    # Warning about depth uptime interpretation
    lines.append(
        "ℹ️ **Depth uptime note**: Depth sampling may operate in effective snapshot mode "
        "if `candidates_limit / max_rps > interval_s`. Uptime is informational only and "
        "NOT a pass/fail criterion. Only best_level_notional and unwind_slippage determine PASS_DEPTH."
    )
    lines.append("")

    # API health summary
    api_health = summarize_api_health(metrics_payload or {})
    run_health = run_meta.get("run_health", api_health.get("run_health", "unknown"))

    lines += [
        "### API Health Summary",
        "",
        f"- **Run health**: {run_health}",
    ]

    if metrics_payload:
        lines += [
            f"- **HTTP 429 (rate limit)**: {api_health.get('http_429_total', 0)}",
            f"- **HTTP 403 (WAF/auth)**: {api_health.get('http_403_total', 0)}",
            f"- **HTTP 5xx (server errors)**: {api_health.get('http_5xx_total', 0)}",
        ]

    lines.append("")
    lines.append("---")
    lines.append("*End of report*")
    lines.append("")

    return "\n".join(lines)


def generate_report(run_dir: Path, cfg: AppConfig) -> None:
    """
    Generate report.md according to TZ_updates.md specification.

    Loads all necessary artifacts and renders the 7+1 section report.
    """
    # Load required artifacts
    summary_path = run_dir / "summary.csv"
    if not summary_path.exists():
        raise FileNotFoundError(f"summary.csv not found in {run_dir}")

    run_meta_path = run_dir / "run_meta.json"
    if not run_meta_path.exists():
        raise FileNotFoundError(f"run_meta.json not found in {run_dir}")

    run_meta = json.loads(run_meta_path.read_text(encoding="utf-8"))
    summary_rows = _read_summary(summary_path)

    # Load optional artifacts
    summary_enriched_path = run_dir / "summary_enriched.csv"
    summary_enriched = (
        _read_summary_enriched(summary_enriched_path)
        if summary_enriched_path.exists()
        else None
    )

    depth_metrics_path = run_dir / "depth_metrics.csv"
    depth_rows = (
        _read_depth_metrics(depth_metrics_path)
        if depth_metrics_path.exists()
        else None
    )

    pipeline_state_path = run_dir / "pipeline_state.json"
    pipeline_state = (
        json.loads(pipeline_state_path.read_text(encoding="utf-8"))
        if pipeline_state_path.exists()
        else None
    )

    metrics_path = run_dir / "metrics.json"
    metrics_payload: dict[str, object] | None = None
    if metrics_path.exists():
        raw_metrics = metrics_path.read_text(encoding="utf-8").strip()
        if raw_metrics:
            metrics_payload = json.loads(raw_metrics)

    # Render report
    report_path = run_dir / "report.md"
    report_path.write_text(
        _render_report(
            run_meta=run_meta,
            pipeline_state=pipeline_state,
            metrics_payload=metrics_payload,
            cfg=cfg,
            summary_rows=summary_rows,
            depth_rows=depth_rows,
            summary_enriched=summary_enriched,
        ),
        encoding="utf-8",
    )

    # Update metrics
    update_metrics(
        metrics_path,
        increments={"report_generated_total": 1},
    )

    # Log completion
    logger = logging.getLogger(__name__)
    log_event(
        logger,
        logging.INFO,
        "report_generated",
        "Report generated",
        path=str(report_path),
    )
