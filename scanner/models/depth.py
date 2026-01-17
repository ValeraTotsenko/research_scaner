"""
Data models for order book depth analysis results.

This module defines immutable dataclasses for storing depth check
results, including per-symbol metrics and aggregate stage results.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DepthSymbolMetrics:
    """
    Aggregated depth metrics for a single trading symbol.

    Contains computed liquidity metrics, pass/fail status for each
    criterion, and reasons for any failures.

    Attributes:
        symbol: Trading pair identifier.
        sample_count: Total depth snapshots attempted.
        valid_samples: Snapshots with valid order book data.
        empty_book_count: Snapshots with empty order book.
        invalid_book_count: Snapshots with malformed book data.
        symbol_unavailable_count: Snapshots where symbol was unavailable.
        best_bid_notional_median: Median best bid liquidity (USDT).
        best_ask_notional_median: Median best ask liquidity (USDT).
        topn_bid_notional_median: Median top-N bid liquidity.
        topn_ask_notional_median: Median top-N ask liquidity.
        band_bid_notional_median: Dict of band_bps -> median notional (bid side).
        band_ask_notional_median: Dict of band_bps -> median notional (ask side).
        unwind_slippage_p90_bps: 90th percentile unwind slippage.
        uptime: Ratio of valid_samples to sample_count.
        best_bid_notional_pass: True if bid liquidity meets threshold.
        best_ask_notional_pass: True if ask liquidity meets threshold.
        unwind_slippage_pass: True if slippage below maximum.
        band_10bps_notional_pass: True if 10bps band meets threshold.
        topn_notional_pass: True if top-N notional meets threshold.
        pass_depth: True if ALL depth criteria pass.
        fail_reasons: Tuple of reason codes for failures.
    """
    symbol: str
    sample_count: int
    valid_samples: int
    empty_book_count: int
    invalid_book_count: int
    symbol_unavailable_count: int
    best_bid_notional_median: float | None
    best_ask_notional_median: float | None
    topn_bid_notional_median: float | None
    topn_ask_notional_median: float | None
    band_bid_notional_median: dict[int, float]
    band_ask_notional_median: dict[int, float]
    unwind_slippage_p90_bps: float | None
    uptime: float
    best_bid_notional_pass: bool
    best_ask_notional_pass: bool
    unwind_slippage_pass: bool
    band_10bps_notional_pass: bool | None
    topn_notional_pass: bool | None
    pass_depth: bool
    fail_reasons: tuple[str, ...]


@dataclass(frozen=True)
class DepthCheckResult:
    """
    Aggregate result from the depth check stage.

    Contains summary statistics across all sampled symbols and
    the per-symbol metrics.

    Attributes:
        target_ticks: Planned number of sampling ticks.
        ticks_success: Ticks with at least one successful snapshot.
        ticks_fail: Ticks with no successful snapshots.
        symbols: Tuple of DepthSymbolMetrics for each candidate.
        depth_requests_total: Total API requests made.
        depth_fail_total: Total failed API requests.
        depth_symbols_pass_total: Count of symbols passing all criteria.
        timed_out: True if stage hit deadline before completion.
        elapsed_s: Total stage duration in seconds.
    """
    target_ticks: int
    ticks_success: int
    ticks_fail: int
    symbols: tuple[DepthSymbolMetrics, ...]
    depth_requests_total: int
    depth_fail_total: int
    depth_symbols_pass_total: int
    timed_out: bool
    elapsed_s: float
