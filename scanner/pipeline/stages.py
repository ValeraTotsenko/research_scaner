from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from scanner.analytics import collect_scoring_metrics, log_scoring_done
from scanner.analytics.scoring import ScoreResult, score_symbol
from scanner.analytics.spread_stats import SpreadSample, SpreadStats, compute_spread_stats
from scanner.config import AppConfig
from scanner.io.export_universe import export_universe
from scanner.io.summary_export import export_summary
from scanner.mexc.client import MexcClient
from scanner.obs.logging import log_event
from scanner.pipeline.depth_check import run_depth_check
from scanner.pipeline.spread_sampling import run_spread_sampling
from scanner.pipeline.ticker_24h import build_ticker24h_stats
from scanner.pipeline.universe import build_universe
from scanner.report.report_md import generate_report
from scanner.validation.artifacts import (
    ValidationResult,
    validate_depth_metrics,
    validate_report_md,
    validate_summary_csv,
    validate_universe,
)

STAGE_ORDER = ["universe", "spread", "score", "depth", "report"]


@dataclass(frozen=True)
class StageContext:
    run_dir: Path
    config: AppConfig
    logger: logging.Logger
    client: MexcClient | None
    metrics_path: Path
    artifact_validation: str
    stage_deadline_ts: float | None = None


@dataclass(frozen=True)
class StageDefinition:
    name: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    run: Callable[[StageContext], dict[str, object] | None]
    validate_inputs: Callable[[StageContext], list[str]]
    validate_outputs: Callable[[StageContext], list[str]]


def _is_strict(ctx: StageContext) -> bool:
    return ctx.artifact_validation == "strict"


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _raw_bookticker_name(cfg: AppConfig) -> str:
    suffix = "jsonl.gz" if cfg.sampling.raw.gzip else "jsonl"
    return f"raw_bookticker.{suffix}"


def _raw_bookticker_path(run_dir: Path, cfg: AppConfig) -> Path:
    return run_dir / _raw_bookticker_name(cfg)


def _load_universe_symbols(run_dir: Path) -> list[str]:
    universe_path = run_dir / "universe.json"
    payload = _load_json(universe_path)
    if not isinstance(payload, dict):
        raise ValueError("universe.json must contain a JSON object")
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("universe.json symbols must be a list")
    return [item for item in symbols if isinstance(item, str)]


def _empty_spread_stats(symbol: str) -> SpreadStats:
    return SpreadStats(
        symbol=symbol,
        sample_count=0,
        valid_samples=0,
        invalid_quotes=0,
        spread_median_bps=None,
        spread_p10_bps=None,
        spread_p25_bps=None,
        spread_p90_bps=None,
        uptime=0.0,
        insufficient_samples=True,
        quote_volume_24h=None,
        quote_volume_24h_raw=None,
        volume_24h_raw=None,
        mid_price=None,
        quote_volume_24h_est=None,
        quote_volume_24h_effective=None,
        trades_24h=None,
        missing_24h_stats=False,
        missing_24h_reason=None,
    )


def _enrich_spread_stats(
    stats: SpreadStats,
    *,
    quote_volume_24h: float | None,
    quote_volume_24h_raw: float | None,
    volume_24h_raw: float | None,
    mid_price: float | None,
    quote_volume_24h_est: float | None,
    quote_volume_24h_effective: float | None,
    trades_24h: int | None,
    missing_24h_stats: bool,
    missing_24h_reason: str | None,
) -> SpreadStats:
    return SpreadStats(
        symbol=stats.symbol,
        sample_count=stats.sample_count,
        valid_samples=stats.valid_samples,
        invalid_quotes=stats.invalid_quotes,
        spread_median_bps=stats.spread_median_bps,
        spread_p10_bps=stats.spread_p10_bps,
        spread_p25_bps=stats.spread_p25_bps,
        spread_p90_bps=stats.spread_p90_bps,
        uptime=stats.uptime,
        insufficient_samples=stats.insufficient_samples,
        quote_volume_24h=quote_volume_24h,
        quote_volume_24h_raw=quote_volume_24h_raw,
        volume_24h_raw=volume_24h_raw,
        mid_price=mid_price,
        quote_volume_24h_est=quote_volume_24h_est,
        quote_volume_24h_effective=quote_volume_24h_effective,
        trades_24h=trades_24h,
        missing_24h_stats=missing_24h_stats,
        missing_24h_reason=missing_24h_reason,
    )


def _read_spread_samples(raw_path: Path, symbols: Iterable[str]) -> dict[str, list[SpreadSample]]:
    symbols_set = set(symbols)
    samples: dict[str, list[SpreadSample]] = {symbol: [] for symbol in symbols_set}
    if raw_path.suffix == ".gz":
        opener = lambda p: gzip.open(p, "rt", encoding="utf-8")  # noqa: E731
    else:
        opener = lambda p: p.open("r", encoding="utf-8")  # noqa: E731

    with opener(raw_path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            symbol = payload.get("symbol")
            if symbol not in symbols_set:
                continue
            bid = _parse_float(payload.get("bid"))
            ask = _parse_float(payload.get("ask"))
            if bid is None or ask is None:
                continue
            samples[symbol].append(SpreadSample(symbol=symbol, bid=bid, ask=ask))

    return samples


def _read_summary_results(run_dir: Path) -> list[ScoreResult]:
    summary_path = run_dir / "summary.json"
    payload = _load_json(summary_path)
    if not isinstance(payload, list):
        raise ValueError("summary.json must contain a list")

    results: list[ScoreResult] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        symbol = entry.get("symbol")
        if not isinstance(symbol, str):
            continue
        fail_reasons = entry.get("fail_reasons") or []
        if isinstance(fail_reasons, list):
            reasons = tuple(str(item) for item in fail_reasons)
        else:
            reasons = tuple(str(fail_reasons).split(";")) if fail_reasons else ()
        stats = SpreadStats(
            symbol=symbol,
            sample_count=0,
            valid_samples=0,
            invalid_quotes=1 if "invalid_quotes" in reasons else 0,
            spread_median_bps=_parse_float(entry.get("spread_median_bps")),
            spread_p10_bps=_parse_float(entry.get("spread_p10_bps")),
            spread_p25_bps=_parse_float(entry.get("spread_p25_bps")),
            spread_p90_bps=_parse_float(entry.get("spread_p90_bps")),
            uptime=_parse_float(entry.get("uptime")) or 0.0,
            insufficient_samples="insufficient_samples" in reasons,
            quote_volume_24h=_parse_float(entry.get("quoteVolume_24h")),
            quote_volume_24h_raw=_parse_float(entry.get("quoteVolume_24h_raw")),
            volume_24h_raw=_parse_float(entry.get("volume_24h_raw")),
            mid_price=_parse_float(entry.get("mid_price")),
            quote_volume_24h_est=_parse_float(entry.get("quoteVolume_24h_est")),
            quote_volume_24h_effective=_parse_float(entry.get("quoteVolume_24h_effective")),
            trades_24h=_parse_int(entry.get("trades_24h")),
            missing_24h_stats=bool(entry.get("missing_24h_stats"))
            if "missing_24h_stats" not in reasons
            else True,
            missing_24h_reason=str(entry.get("missing_24h_reason"))
            if entry.get("missing_24h_reason")
            else None,
        )
        results.append(
            ScoreResult(
                symbol=symbol,
                spread_stats=stats,
                net_edge_bps=_parse_float(entry.get("net_edge_bps")),
                pass_spread=bool(entry.get("pass_spread")),
                score=_parse_float(entry.get("score")) or 0.0,
                fail_reasons=reasons,
            )
        )
    return results


def _validate_inputs_universe(_: StageContext) -> list[str]:
    return []


def _validate_outputs_universe(ctx: StageContext) -> list[str]:
    errors: list[str] = []
    strict = _is_strict(ctx)
    universe_path = ctx.run_dir / "universe.json"
    result = validate_universe(universe_path, strict=strict)
    if not result.valid:
        errors.append(result.error or "universe.json invalid")
    rejects_path = ctx.run_dir / "universe_rejects.csv"
    if not rejects_path.exists():
        errors.append("Missing universe_rejects.csv")
    return errors


def _validate_inputs_spread(ctx: StageContext) -> list[str]:
    return _validate_outputs_universe(ctx)


def _validate_outputs_spread(ctx: StageContext) -> list[str]:
    raw_path = _raw_bookticker_path(ctx.run_dir, ctx.config)
    if not raw_path.exists():
        return [f"Missing {raw_path.name}"]
    if _is_strict(ctx) and raw_path.stat().st_size == 0:
        return [f"{raw_path.name} is empty"]
    return []


def _validate_inputs_score(ctx: StageContext) -> list[str]:
    errors = _validate_outputs_universe(ctx)
    raw_errors = _validate_outputs_spread(ctx)
    return errors + raw_errors


def _validate_outputs_score(ctx: StageContext) -> list[str]:
    result = validate_summary_csv(ctx.run_dir / "summary.csv", strict=_is_strict(ctx))
    if not result.valid:
        return [result.error or "summary.csv invalid"]
    summary_json = ctx.run_dir / "summary.json"
    if not summary_json.exists():
        return ["Missing summary.json"]
    return []


def _validate_inputs_depth(ctx: StageContext) -> list[str]:
    return _validate_outputs_score(ctx)


def _validate_outputs_depth(ctx: StageContext) -> list[str]:
    result = validate_depth_metrics(
        ctx.run_dir / "depth_metrics.csv",
        band_bps=ctx.config.depth.band_bps,
        strict=_is_strict(ctx),
    )
    if not result.valid:
        return [result.error or "depth_metrics.csv invalid"]
    summary_enriched = ctx.run_dir / "summary_enriched.csv"
    if not summary_enriched.exists():
        return ["Missing summary_enriched.csv"]
    return []


def _validate_inputs_report(ctx: StageContext) -> list[str]:
    errors: list[str] = []
    result = validate_summary_csv(ctx.run_dir / "summary.csv", strict=_is_strict(ctx))
    if not result.valid:
        errors.append(result.error or "summary.csv invalid")
    run_meta = ctx.run_dir / "run_meta.json"
    if not run_meta.exists():
        errors.append("Missing run_meta.json")
    return errors


def _validate_outputs_report(ctx: StageContext) -> list[str]:
    result = validate_report_md(ctx.run_dir / "report.md", strict=_is_strict(ctx))
    if not result.valid:
        return [result.error or "report.md invalid"]
    shortlist_path = ctx.run_dir / "shortlist.csv"
    if not shortlist_path.exists():
        return ["Missing shortlist.csv"]
    return []


def _run_universe(ctx: StageContext) -> dict[str, object]:
    if ctx.client is None:
        raise RuntimeError("MEXC client required for universe stage")
    result = build_universe(
        ctx.client,
        ctx.config.universe,
        logger=ctx.logger,
        metrics_path=ctx.metrics_path,
    )
    export_universe(ctx.run_dir, result)
    return {
        "symbols_total": result.stats.total,
        "symbols_kept": result.stats.kept,
        "symbols_rejected": result.stats.rejected,
    }


def _run_spread(ctx: StageContext) -> dict[str, object]:
    if ctx.client is None:
        raise RuntimeError("MEXC client required for spread stage")
    symbols = _load_universe_symbols(ctx.run_dir)
    result = run_spread_sampling(
        ctx.client,
        symbols,
        ctx.config.sampling,
        ctx.run_dir,
        deadline_ts=ctx.stage_deadline_ts,
    )
    return {
        "ticks_total": result.ticks_success + result.ticks_fail,
        "ticks_success": result.ticks_success,
        "ticks_fail": result.ticks_fail,
        "uptime": result.uptime,
        "invalid_quotes": result.invalid_quotes,
        "missing_quotes": result.missing_quotes,
        "timed_out": result.timed_out,
        "elapsed_s": result.elapsed_s,
    }


def _run_score(ctx: StageContext) -> dict[str, object]:
    if ctx.client is None:
        raise RuntimeError("MEXC client required for score stage")
    symbols = _load_universe_symbols(ctx.run_dir)
    raw_path = _raw_bookticker_path(ctx.run_dir, ctx.config)
    samples_by_symbol = _read_spread_samples(raw_path, symbols)
    ticker_payload = ctx.client.get_ticker_24hr()
    book_payload = ctx.client.get_book_ticker()
    ticker_stats = build_ticker24h_stats(
        ticker_payload,
        book_payload,
        symbols=symbols,
        use_quote_volume_estimate=ctx.config.universe.use_quote_volume_estimate,
        require_trade_count=ctx.config.universe.require_trade_count,
        logger=ctx.logger,
        log_summary=False,
    )

    results: list[ScoreResult] = []
    for symbol in symbols:
        samples = samples_by_symbol.get(symbol, [])
        if samples:
            stats = compute_spread_stats(samples)
        else:
            stats = _empty_spread_stats(symbol)
        ticker = ticker_stats[symbol]
        stats = _enrich_spread_stats(
            stats,
            quote_volume_24h=ticker.quote_volume_effective,
            quote_volume_24h_raw=ticker.quote_volume_raw,
            volume_24h_raw=ticker.volume_raw,
            mid_price=ticker.mid_price,
            quote_volume_24h_est=ticker.quote_volume_est,
            quote_volume_24h_effective=ticker.quote_volume_effective,
            trades_24h=ticker.trade_count,
            missing_24h_stats=ticker.missing_24h_stats,
            missing_24h_reason=ticker.missing_24h_reason,
        )
        results.append(score_symbol(stats, ctx.config))

    export_summary(ctx.run_dir, results, logger=ctx.logger)
    log_scoring_done(ctx.logger, results)
    metrics = collect_scoring_metrics(results)
    metrics["symbols_scored"] = len(results)
    return metrics


def _run_depth(ctx: StageContext) -> dict[str, object]:
    if ctx.client is None:
        raise RuntimeError("MEXC client required for depth stage")
    candidates = _read_summary_results(ctx.run_dir)
    result = run_depth_check(
        ctx.client,
        candidates,
        ctx.config,
        ctx.run_dir,
        deadline_ts=ctx.stage_deadline_ts,
    )
    return {
        "ticks_total": result.ticks_success + result.ticks_fail,
        "ticks_success": result.ticks_success,
        "ticks_fail": result.ticks_fail,
        "depth_requests_total": result.depth_requests_total,
        "depth_fail_total": result.depth_fail_total,
        "depth_symbols_pass_total": result.depth_symbols_pass_total,
        "timed_out": result.timed_out,
        "elapsed_s": result.elapsed_s,
    }


def _run_report(ctx: StageContext) -> dict[str, object]:
    generate_report(ctx.run_dir, ctx.config)
    log_event(ctx.logger, logging.INFO, "report_done", "Report stage finished")
    return {}


def default_stage_definitions(cfg: AppConfig) -> list[StageDefinition]:
    spread_raw = _raw_bookticker_name(cfg)
    return [
        StageDefinition(
            name="universe",
            inputs=(),
            outputs=("universe.json", "universe_rejects.csv"),
            run=_run_universe,
            validate_inputs=_validate_inputs_universe,
            validate_outputs=_validate_outputs_universe,
        ),
        StageDefinition(
            name="spread",
            inputs=("universe.json",),
            outputs=(spread_raw,),
            run=_run_spread,
            validate_inputs=_validate_inputs_spread,
            validate_outputs=_validate_outputs_spread,
        ),
        StageDefinition(
            name="score",
            inputs=("universe.json", spread_raw),
            outputs=("summary.csv", "summary.json"),
            run=_run_score,
            validate_inputs=_validate_inputs_score,
            validate_outputs=_validate_outputs_score,
        ),
        StageDefinition(
            name="depth",
            inputs=("summary.csv",),
            outputs=("depth_metrics.csv", "summary_enriched.csv"),
            run=_run_depth,
            validate_inputs=_validate_inputs_depth,
            validate_outputs=_validate_outputs_depth,
        ),
        StageDefinition(
            name="report",
            inputs=("summary.csv", "run_meta.json"),
            outputs=("report.md", "shortlist.csv"),
            run=_run_report,
            validate_inputs=_validate_inputs_report,
            validate_outputs=_validate_outputs_report,
        ),
    ]


def validate_stage_names(stage_names: Iterable[str]) -> list[str]:
    allowed = set(STAGE_ORDER)
    invalid = [name for name in stage_names if name not in allowed]
    if invalid:
        raise ValueError(f"Unknown stages: {', '.join(invalid)}")
    return list(stage_names)


def ensure_stage_order(stage_names: Sequence[str]) -> None:
    positions = {name: idx for idx, name in enumerate(STAGE_ORDER)}
    last = -1
    for name in stage_names:
        idx = positions[name]
        if idx < last:
            raise ValueError("Stages must follow fixed order: " + " -> ".join(STAGE_ORDER))
        last = idx
