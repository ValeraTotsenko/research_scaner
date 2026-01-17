"""
Spread statistics computation module for bid-ask spread analysis.

This module calculates statistical metrics from raw bid/ask price samples,
including median spread, percentiles (P10, P25, P90), and data quality
indicators like uptime and invalid quote detection.

Key Metrics:
    - **Spread (bps)**: (ask - bid) / mid × 10,000
    - **Uptime**: valid_samples / total_samples (quote availability ratio)
    - **Percentiles**: Linear interpolation method for P10, P25, P50 (median), P90

Data Quality:
    - Invalid quotes: bid >= ask or mid <= 0
    - Insufficient samples: fewer than MIN_SAMPLE_COUNT (3) valid samples

The spread formula uses the mid-price as denominator to normalize spreads
across different price levels, making them comparable across trading pairs.

Example:
    >>> samples = [SpreadSample("BTCUSDT", 50000.0, 50010.0), ...]
    >>> stats = compute_spread_stats(samples)
    >>> print(f"Median spread: {stats.spread_median_bps:.2f} bps")
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Sequence

from scanner.models.spread import compute_spread_bps

# Minimum number of valid samples required for meaningful statistics
MIN_SAMPLE_COUNT = 3


@dataclass(frozen=True)
class SpreadSample:
    """
    Single bid/ask price observation for a trading pair.

    Attributes:
        symbol: Trading pair identifier (e.g., "BTCUSDT").
        bid: Best bid price (highest buy order).
        ask: Best ask price (lowest sell order).
    """
    symbol: str
    bid: float
    ask: float


@dataclass(frozen=True)
class SpreadStats:
    """
    Comprehensive spread statistics for a trading pair.

    Contains computed spread metrics (median, percentiles), data quality
    indicators, and optional 24-hour market statistics for enrichment.

    Core Spread Metrics:
        symbol: Trading pair identifier.
        sample_count: Total number of samples collected.
        valid_samples: Samples with valid bid/ask quotes.
        invalid_quotes: Count of samples with bid >= ask or mid <= 0.
        spread_median_bps: Median spread in basis points.
        spread_p10_bps: 10th percentile spread (tight spread conditions).
        spread_p25_bps: 25th percentile spread.
        spread_p90_bps: 90th percentile spread (wide spread/volatile conditions).
        uptime: Ratio of valid_samples to sample_count (0.0 to 1.0).
        insufficient_samples: True if valid_samples < MIN_SAMPLE_COUNT.

    24-Hour Market Data (enriched from ticker API):
        quote_volume_24h: Quote volume used for filtering (effective value).
        quote_volume_24h_raw: Raw quoteVolume from API response.
        volume_24h_raw: Raw volume from API response.
        mid_price: Current mid price for volume estimation.
        quote_volume_24h_est: Estimated quote volume (volume × mid_price).
        quote_volume_24h_effective: Final quote volume (raw or estimated).
        trades_24h: Number of trades in last 24 hours.
        missing_24h_stats: True if 24h data unavailable.
        missing_24h_reason: Explanation for missing 24h data.
    """
    symbol: str | None
    sample_count: int
    valid_samples: int
    invalid_quotes: int
    spread_median_bps: float | None
    spread_p10_bps: float | None
    spread_p25_bps: float | None
    spread_p90_bps: float | None
    uptime: float
    insufficient_samples: bool
    quote_volume_24h: float | None = None
    quote_volume_24h_raw: float | None = None
    volume_24h_raw: float | None = None
    mid_price: float | None = None
    quote_volume_24h_est: float | None = None
    quote_volume_24h_effective: float | None = None
    trades_24h: int | None = None
    missing_24h_stats: bool = False
    missing_24h_reason: str | None = None


def _percentile(sorted_values: Sequence[float], percentile: float) -> float:
    """
    Calculate percentile using linear interpolation method.

    This implementation matches the specification in docs/spec.md and uses
    linear interpolation between adjacent values when the position falls
    between indices.

    Algorithm:
        1. Compute position = percentile × (n - 1)
        2. Find lower and upper indices
        3. Interpolate: lower_val + (upper_val - lower_val) × fractional_part

    Args:
        sorted_values: Pre-sorted sequence of numeric values (ascending).
        percentile: Percentile to compute (0.0 to 1.0, e.g., 0.9 for P90).

    Returns:
        Interpolated percentile value.

    Raises:
        ValueError: If sorted_values is empty or percentile not in [0, 1].

    Example:
        >>> _percentile([10, 20, 30, 40, 50], 0.25)  # P25
        20.0
        >>> _percentile([10, 20, 30, 40, 50], 0.5)   # P50 (median)
        30.0
    """
    if not sorted_values:
        raise ValueError("Percentile requires at least one value")
    if not 0 <= percentile <= 1:
        raise ValueError("Percentile must be between 0 and 1")
    if len(sorted_values) == 1:
        return sorted_values[0]

    # Calculate position in the array using linear interpolation
    position = percentile * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]

    # Linear interpolation between adjacent values
    weight = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * weight


def compute_spread_stats(samples: Sequence[SpreadSample]) -> SpreadStats:
    """
    Compute statistical metrics from a collection of spread samples.

    Processes raw bid/ask samples to calculate spread distribution metrics
    (median, percentiles) and data quality indicators. Invalid quotes
    (bid >= ask or mid <= 0) are counted but excluded from statistics.

    Processing Steps:
        1. Extract symbol from first valid sample
        2. Convert each sample to spread_bps, tracking invalid quotes
        3. Calculate uptime ratio and insufficient_samples flag
        4. Compute percentiles from valid spreads (or None if empty)

    Args:
        samples: Sequence of SpreadSample objects from spread sampling.

    Returns:
        SpreadStats with computed metrics. Note that 24-hour market data
        fields are not populated here; use score stage enrichment for those.

    Raises:
        ValueError: If samples sequence is empty.

    Note:
        - Returns SpreadStats with None percentiles if all samples are invalid
        - Sets insufficient_samples=True if valid_samples < MIN_SAMPLE_COUNT
        - 24h stats fields are left at defaults (populated during enrichment)
    """
    if not samples:
        raise ValueError("No samples provided for spread stats")

    # Extract symbol from first sample that has one
    symbol = next((sample.symbol for sample in samples if sample.symbol), None)
    spreads: list[float] = []
    invalid_quotes = 0

    # Convert each sample to spread_bps, tracking failures
    for sample in samples:
        try:
            spread_bps = compute_spread_bps(sample.bid, sample.ask)
        except ValueError:
            # Invalid quote: bid >= ask or mid <= 0
            invalid_quotes += 1
            continue
        spreads.append(spread_bps)

    sample_count = len(samples)
    valid_samples = len(spreads)
    # Uptime = fraction of samples that produced valid spreads
    uptime = valid_samples / sample_count if sample_count else 0.0
    insufficient_samples = valid_samples < MIN_SAMPLE_COUNT

    # Compute percentiles only if we have valid data
    if spreads:
        spreads_sorted = sorted(spreads)
        spread_median_bps = statistics.median(spreads_sorted)
        spread_p10_bps = _percentile(spreads_sorted, 0.10)
        spread_p25_bps = _percentile(spreads_sorted, 0.25)
        spread_p90_bps = _percentile(spreads_sorted, 0.90)
    else:
        spread_median_bps = None
        spread_p10_bps = None
        spread_p25_bps = None
        spread_p90_bps = None

    return SpreadStats(
        symbol=symbol,
        sample_count=sample_count,
        valid_samples=valid_samples,
        invalid_quotes=invalid_quotes,
        spread_median_bps=spread_median_bps,
        spread_p10_bps=spread_p10_bps,
        spread_p25_bps=spread_p25_bps,
        spread_p90_bps=spread_p90_bps,
        uptime=uptime,
        insufficient_samples=insufficient_samples,
    )
