from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from scanner.analytics.spread_stats import SpreadStats
from scanner.config import AppConfig
from scanner.obs.logging import log_event


@dataclass(frozen=True)
class ScoreResult:
    symbol: str
    spread_stats: SpreadStats
    net_edge_bps: float | None
    pass_spread: bool
    score: float
    fail_reasons: tuple[str, ...]


def _net_edge_bps(stats: SpreadStats, cfg: AppConfig) -> float | None:
    if stats.spread_median_bps is None:
        return None
    return stats.spread_median_bps - (cfg.fees.maker_bps + cfg.fees.taker_bps)


def score_symbol(stats: SpreadStats, cfg: AppConfig) -> ScoreResult:
    symbol = stats.symbol or "UNKNOWN"
    fail_reasons: list[str] = []

    if stats.insufficient_samples:
        fail_reasons.append("insufficient_samples")
    if stats.invalid_quotes > 0:
        fail_reasons.append("invalid_quotes")
    if stats.uptime < cfg.thresholds.uptime_min:
        fail_reasons.append("low_uptime")

    if stats.spread_median_bps is None or stats.spread_p90_bps is None:
        if "insufficient_samples" not in fail_reasons:
            fail_reasons.append("insufficient_samples")
    else:
        if stats.spread_median_bps > cfg.thresholds.spread.median_max_bps:
            fail_reasons.append("spread_median_high")
        if stats.spread_p90_bps > cfg.thresholds.spread.p90_max_bps:
            fail_reasons.append("spread_p90_high")

    if stats.quote_volume_24h is None or stats.trades_24h is None:
        fail_reasons.append("missing_24h_stats")

    net_edge_bps = _net_edge_bps(stats, cfg)

    volatility_penalty = 0.0
    if stats.spread_p90_bps is not None and stats.spread_p10_bps is not None:
        volatility_penalty = max(stats.spread_p90_bps - stats.spread_p10_bps, 0.0)

    base_edge = max(net_edge_bps or 0.0, 0.0)
    score = base_edge + stats.uptime * 100 - volatility_penalty

    pass_spread = (
        stats.spread_median_bps is not None
        and stats.spread_p90_bps is not None
        and stats.uptime >= cfg.thresholds.uptime_min
        and stats.invalid_quotes == 0
        and not stats.insufficient_samples
        and stats.spread_median_bps <= cfg.thresholds.spread.median_max_bps
        and stats.spread_p90_bps <= cfg.thresholds.spread.p90_max_bps
    )

    return ScoreResult(
        symbol=symbol,
        spread_stats=stats,
        net_edge_bps=net_edge_bps,
        pass_spread=pass_spread,
        score=score,
        fail_reasons=tuple(fail_reasons),
    )


def collect_scoring_metrics(results: Iterable[ScoreResult]) -> dict[str, int]:
    pass_spread = 0
    fail_spread = 0
    insufficient_samples = 0

    for result in results:
        if result.pass_spread:
            pass_spread += 1
        else:
            fail_spread += 1
        if result.spread_stats.insufficient_samples:
            insufficient_samples += 1

    return {
        "symbols_pass_spread": pass_spread,
        "symbols_fail_spread": fail_spread,
        "symbols_insufficient_samples": insufficient_samples,
    }


def log_scoring_done(logger: logging.Logger, results: Iterable[ScoreResult], *, top_n: int = 5) -> None:
    results_list = list(results)
    pass_count = sum(1 for result in results_list if result.pass_spread)
    fail_count = len(results_list) - pass_count
    top_symbols = [
        result.symbol
        for result in sorted(results_list, key=lambda item: (-item.score, item.symbol))[:top_n]
    ]

    log_event(
        logger,
        logging.INFO,
        "scoring_done",
        "Scoring completed",
        pass_count=pass_count,
        fail_count=fail_count,
        top_symbols=top_symbols,
    )
