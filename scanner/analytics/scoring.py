"""
Scoring and edge calculation module for spread feasibility analysis.

This module implements the core business logic for evaluating trading pair viability
based on spread statistics, fee structure, and quality thresholds. It determines
which symbols pass the spread criteria and calculates the net edge metrics.

Key Concepts:
    - **Edge MM (Maker/Maker)**: Expected profit assuming maker fills on both sides.
      Formula: spread_median_bps - 2 × maker_fee_bps - slippage_buffer_bps

    - **Edge with Unwind**: Expected profit with emergency taker exit.
      Formula: spread_median_bps - (maker_fee_bps + taker_fee_bps) - slippage_buffer_bps

    - **Score**: Composite ranking metric combining edge, uptime, and volatility penalty.
      Formula: max(edge_mm_bps, 0) + uptime × 100 - volatility_penalty

Fail Reasons:
    - insufficient_samples: Not enough valid spread samples collected
    - invalid_quotes: Quotes with bid >= ask or non-positive mid price detected
    - low_uptime: Quote availability below minimum threshold
    - spread_median_low: Median spread below minimum viable threshold
    - spread_median_high: Median spread exceeds maximum acceptable threshold
    - spread_p90_low: 90th percentile spread below minimum
    - spread_p90_high: 90th percentile spread exceeds maximum (too volatile)
    - missing_24h_stats: 24-hour volume/trade statistics unavailable

Example:
    >>> stats = compute_spread_stats(samples)
    >>> result = score_symbol(stats, config)
    >>> if result.pass_spread:
    ...     print(f"{result.symbol}: edge={result.edge_mm_bps:.2f} bps")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from scanner.analytics.spread_stats import SpreadStats
from scanner.config import AppConfig
from scanner.obs.logging import log_event


@dataclass(frozen=True)
class ScoreResult:
    """
    Immutable result of symbol scoring containing edge metrics and pass/fail status.

    This dataclass aggregates all scoring outputs for a single trading pair,
    including computed edge values, spread statistics reference, and fail reasons.

    Attributes:
        symbol: Trading pair identifier (e.g., "BTCUSDT").
        spread_stats: Source spread statistics used for scoring.
        edge_mm_bps: Maker/Maker edge in basis points (None if insufficient data).
        edge_with_unwind_bps: Edge with taker unwind in basis points.
        net_edge_bps: Primary edge metric (currently equals edge_mm_bps).
        pass_spread: True if symbol meets all spread criteria.
        score: Composite ranking score for prioritization.
        fail_reasons: Tuple of reason codes explaining any failures.
    """
    symbol: str
    spread_stats: SpreadStats
    edge_mm_bps: float | None
    edge_with_unwind_bps: float | None
    net_edge_bps: float | None
    pass_spread: bool
    score: float
    fail_reasons: tuple[str, ...]


def _edge_mm_bps(stats: SpreadStats, cfg: AppConfig) -> float | None:
    """
    Calculate maker/maker edge assuming maker fills on both entry and exit.

    This is the primary edge metric for normal spread-capture operation where
    the strategy places passive limit orders on both sides.

    Args:
        stats: Spread statistics containing median spread value.
        cfg: Application config with fee structure and slippage buffer.

    Returns:
        Edge in basis points, or None if spread_median_bps is unavailable.

    Formula:
        edge_mm = spread_median - (2 × maker_fee) - slippage_buffer
    """
    if stats.spread_median_bps is None:
        return None
    # Deduct maker fees for both legs (buy + sell) plus safety buffer
    return (
        stats.spread_median_bps
        - 2 * cfg.fees.maker_bps
        - cfg.thresholds.slippage_buffer_bps
    )


def _edge_with_unwind_bps(stats: SpreadStats, cfg: AppConfig) -> float | None:
    """
    Calculate edge for emergency unwind scenario with forced taker exit.

    This represents the worst-case edge when position must be closed immediately
    via market order (taker) instead of waiting for passive fill.

    Args:
        stats: Spread statistics containing median spread value.
        cfg: Application config with fee structure and slippage buffer.

    Returns:
        Edge in basis points, or None if spread_median_bps is unavailable.

    Formula:
        edge_unwind = spread_median - (maker_fee + taker_fee) - slippage_buffer
    """
    if stats.spread_median_bps is None:
        return None
    # One maker leg (entry) + one taker leg (emergency exit)
    return (
        stats.spread_median_bps
        - (cfg.fees.maker_bps + cfg.fees.taker_bps)
        - cfg.thresholds.slippage_buffer_bps
    )


def _net_edge_bps(stats: SpreadStats, cfg: AppConfig) -> float | None:
    """
    Return the primary net edge metric used for pass/fail evaluation.

    Currently delegates to edge_mm_bps (maker/maker model) as this reflects
    the expected operating mode for spread-capture strategies.

    Args:
        stats: Spread statistics containing median spread value.
        cfg: Application config with fee structure.

    Returns:
        Net edge in basis points (same as edge_mm_bps).
    """
    # Use edge_mm_bps (maker/maker model) as the primary edge metric.
    # This reflects normal spread-capture operation where we're maker on both sides.
    return _edge_mm_bps(stats, cfg)


def score_symbol(stats: SpreadStats, cfg: AppConfig) -> ScoreResult:
    """
    Evaluate a symbol's spread statistics and determine pass/fail status.

    This is the main scoring function that applies all threshold checks,
    calculates edge metrics, and produces a composite score for ranking.

    The scoring process:
        1. Check data quality (samples, quotes, uptime)
        2. Verify spread is within acceptable corridor (min/max thresholds)
        3. Calculate edge metrics (MM and unwind scenarios)
        4. Compute composite score for prioritization

    Args:
        stats: Spread statistics for the symbol (from compute_spread_stats).
        cfg: Application config with thresholds, fees, and slippage settings.

    Returns:
        ScoreResult with edge metrics, pass/fail status, score, and fail reasons.

    Note:
        A symbol passes spread criteria only if ALL conditions are met:
        - Sufficient valid samples collected
        - No invalid quotes detected
        - Uptime above minimum threshold
        - Spread median within [min_bps, max_bps] corridor
        - Spread P90 within [min_bps, max_bps] corridor
    """
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
        if stats.spread_median_bps < cfg.thresholds.spread.median_min_bps:
            fail_reasons.append("spread_median_low")
        if stats.spread_median_bps > cfg.thresholds.spread.median_max_bps:
            fail_reasons.append("spread_median_high")
        if stats.spread_p90_bps < cfg.thresholds.spread.p90_min_bps:
            fail_reasons.append("spread_p90_low")
        if stats.spread_p90_bps > cfg.thresholds.spread.p90_max_bps:
            fail_reasons.append("spread_p90_high")

    if stats.missing_24h_stats:
        fail_reasons.append("missing_24h_stats")

    edge_mm_bps = _edge_mm_bps(stats, cfg)
    edge_with_unwind_bps = _edge_with_unwind_bps(stats, cfg)
    net_edge_bps = _net_edge_bps(stats, cfg)

    volatility_penalty = 0.0
    if stats.spread_p90_bps is not None and stats.spread_p10_bps is not None:
        volatility_penalty = max(stats.spread_p90_bps - stats.spread_p10_bps, 0.0)

    base_edge = max(edge_mm_bps or 0.0, 0.0)
    score = base_edge + stats.uptime * 100 - volatility_penalty

    pass_spread = (
        stats.spread_median_bps is not None
        and stats.spread_p90_bps is not None
        and stats.uptime >= cfg.thresholds.uptime_min
        and stats.invalid_quotes == 0
        and not stats.insufficient_samples
        and stats.spread_median_bps >= cfg.thresholds.spread.median_min_bps
        and stats.spread_median_bps <= cfg.thresholds.spread.median_max_bps
        and stats.spread_p90_bps >= cfg.thresholds.spread.p90_min_bps
        and stats.spread_p90_bps <= cfg.thresholds.spread.p90_max_bps
    )

    return ScoreResult(
        symbol=symbol,
        spread_stats=stats,
        edge_mm_bps=edge_mm_bps,
        edge_with_unwind_bps=edge_with_unwind_bps,
        net_edge_bps=net_edge_bps,
        pass_spread=pass_spread,
        score=score,
        fail_reasons=tuple(fail_reasons),
    )


def collect_scoring_metrics(results: Iterable[ScoreResult]) -> dict[str, int]:
    """
    Aggregate scoring results into summary metrics for pipeline state.

    Args:
        results: Iterable of ScoreResult objects from score_symbol.

    Returns:
        Dictionary with aggregate counts:
        - symbols_pass_spread: Number of symbols passing all spread criteria
        - symbols_fail_spread: Number of symbols failing spread criteria
        - symbols_insufficient_samples: Count with insufficient sample data
    """
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
    """
    Log scoring completion event with summary statistics and top symbols.

    Emits a structured log event containing pass/fail counts and the
    highest-scoring symbols for quick visibility.

    Args:
        logger: Logger instance for event output.
        results: Iterable of ScoreResult objects to summarize.
        top_n: Number of top-scoring symbols to include (default: 5).
    """
    results_list = list(results)
    pass_count = sum(1 for result in results_list if result.pass_spread)
    fail_count = len(results_list) - pass_count
    # Sort by score descending, then alphabetically for ties
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
