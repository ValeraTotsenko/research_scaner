"""
Data models and utilities for spread sampling results.

This module defines the spread sampling result dataclass and the
core spread calculation function used throughout the scanner.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SpreadSampleResult:
    """
    Aggregate result from the spread sampling stage.

    Contains summary statistics about the sampling run including
    success/failure counts and data quality indicators.

    Attributes:
        target_ticks: Planned number of sampling intervals.
        ticks_success: Intervals with successful bid/ask capture.
        ticks_fail: Intervals with API failures or timeouts.
        invalid_quotes: Quotes with bid >= ask or invalid mid.
        missing_quotes: Symbols missing from API response.
        uptime: Ratio of ticks_success to target_ticks.
        low_quality: True if uptime below minimum threshold.
        raw_path: Path to raw_bookticker.jsonl[.gz] output.
        timed_out: True if stage hit deadline before completion.
        elapsed_s: Total stage duration in seconds.
    """
    target_ticks: int
    ticks_success: int
    ticks_fail: int
    invalid_quotes: int
    missing_quotes: int
    uptime: float
    low_quality: bool
    raw_path: Path | None
    timed_out: bool
    elapsed_s: float


def compute_spread_bps(bid: float, ask: float) -> float:
    """
    Calculate bid-ask spread in basis points.

    Uses mid-price as denominator to normalize spreads across
    different price levels, making them comparable.

    Formula:
        spread_bps = (ask - bid) / mid × 10,000
        where mid = (bid + ask) / 2

    Args:
        bid: Best bid price.
        ask: Best ask price.

    Returns:
        Spread in basis points (1 bp = 0.01%).

    Raises:
        ValueError: If mid price is not positive (indicates invalid quote).

    Example:
        >>> compute_spread_bps(100.0, 100.10)
        10.0  # 10 bps = 0.1% spread
    """
    mid = (bid + ask) / 2
    if mid <= 0:
        raise ValueError("Mid price must be positive")
    return (ask - bid) / mid * 10_000
