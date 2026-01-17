"""
Order book depth metrics computation module.

This module analyzes order book data to evaluate liquidity characteristics
and compute slippage estimates for position sizing and risk assessment.

Key Metrics:
    - **Best Level Notional**: Liquidity at top of book (bid × qty, ask × qty)
    - **Top-N Notional**: Cumulative liquidity across first N price levels
    - **Band Notional**: Liquidity within X basis points of mid price
    - **Unwind Slippage**: Expected slippage for emergency position exit

Slippage Calculation:
    Simulates selling `stress_notional` worth of asset into the bid side,
    walking through price levels until filled. Slippage is the difference
    between mid price and volume-weighted average execution price.

    slippage_bps = (mid_price - avg_execution_price) / mid_price × 10,000

Band Depth Analysis:
    Measures liquidity within specified basis point bands from mid price.
    For example, band_bps=[10, 25, 50] calculates notional within 10bps,
    25bps, and 50bps of the current mid price.

Example:
    >>> metrics = compute_snapshot_metrics(bids, asks, top_n=5, band_bps=[10, 25], stress_notional=1000)
    >>> print(f"Best bid liquidity: ${metrics.best_bid_notional:.2f}")
    >>> print(f"Unwind slippage: {metrics.unwind_slippage_bps:.2f} bps")
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class DepthSnapshotMetrics:
    """
    Computed metrics from a single order book snapshot.

    Attributes:
        best_bid_notional: Notional value at best bid (price × quantity).
        best_ask_notional: Notional value at best ask (price × quantity).
        topn_bid_notional: Cumulative bid notional across top N levels.
        topn_ask_notional: Cumulative ask notional across top N levels.
        band_bid_notional: Dict mapping band_bps -> notional within that band.
        unwind_slippage_bps: Estimated slippage for stress_notional sell, or None
            if insufficient liquidity to complete the simulated trade.
    """
    best_bid_notional: float
    best_ask_notional: float
    topn_bid_notional: float
    topn_ask_notional: float
    band_bid_notional: dict[int, float]
    unwind_slippage_bps: float | None


def _percentile(sorted_values: Sequence[float], percentile: float) -> float:
    """
    Calculate percentile using linear interpolation (same algorithm as spread_stats).

    Args:
        sorted_values: Pre-sorted sequence of values (ascending order).
        percentile: Percentile to compute (0.0 to 1.0).

    Returns:
        Interpolated percentile value.

    Raises:
        ValueError: If values empty or percentile out of range.
    """
    if not sorted_values:
        raise ValueError("Percentile requires at least one value")
    if not 0 <= percentile <= 1:
        raise ValueError("Percentile must be between 0 and 1")
    if len(sorted_values) == 1:
        return sorted_values[0]

    position = percentile * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]

    weight = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * weight


def _parse_levels(levels: Iterable[Sequence[object]]) -> list[tuple[float, float]]:
    """
    Parse raw order book levels into typed (price, quantity) tuples.

    Validates each level has at least 2 elements (price, qty) and both
    are positive numbers. This handles the raw API response format where
    levels are typically [["price_str", "qty_str"], ...].

    Args:
        levels: Iterable of sequences, each containing [price, quantity, ...].

    Returns:
        List of (price, quantity) tuples with validated positive floats.

    Raises:
        ValueError: If any level has fewer than 2 elements, non-numeric values,
            or non-positive price/quantity.
    """
    parsed: list[tuple[float, float]] = []
    for entry in levels:
        if len(entry) < 2:
            raise ValueError("Depth level must have price and quantity")
        try:
            price = float(entry[0])
            qty = float(entry[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("Depth level price/qty must be numeric") from exc
        if price <= 0 or qty <= 0:
            raise ValueError("Depth level price/qty must be positive")
        parsed.append((price, qty))
    return parsed


def compute_snapshot_metrics(
    bids_raw: Iterable[Sequence[object]],
    asks_raw: Iterable[Sequence[object]],
    *,
    top_n: int,
    band_bps: Iterable[int],
    stress_notional: float,
) -> DepthSnapshotMetrics:
    """
    Compute all depth metrics from a single order book snapshot.

    Analyzes bid and ask sides to calculate liquidity metrics at various
    depths and estimates slippage for stress scenario position unwinding.

    Computation Steps:
        1. Parse and validate raw bid/ask levels
        2. Calculate mid price from best bid/ask
        3. Compute best level and top-N notional values
        4. Calculate band notional for each specified basis point band
        5. Simulate emergency unwind to estimate slippage

    Args:
        bids_raw: Raw bid levels from API [[price, qty], ...], sorted price descending.
        asks_raw: Raw ask levels from API [[price, qty], ...], sorted price ascending.
        top_n: Number of levels to include in top-N notional calculation.
        band_bps: Basis point bands for band depth analysis (e.g., [10, 25, 50]).
        stress_notional: Notional value (in quote currency) for slippage simulation.

    Returns:
        DepthSnapshotMetrics with all computed liquidity metrics.

    Raises:
        ValueError: If book is empty, parameters invalid, or mid price non-positive.
    """
    bids = _parse_levels(bids_raw)
    asks = _parse_levels(asks_raw)
    if not bids or not asks:
        raise ValueError("Empty book")
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if stress_notional <= 0:
        raise ValueError("stress_notional must be positive")

    # Extract best bid/ask and calculate mid price
    best_bid_price, best_bid_qty = bids[0]
    best_ask_price, best_ask_qty = asks[0]
    mid = (best_bid_price + best_ask_price) / 2
    if mid <= 0:
        raise ValueError("Mid price must be positive")

    # Best level liquidity (single price level)
    best_bid_notional = best_bid_price * best_bid_qty
    best_ask_notional = best_ask_price * best_ask_qty

    # Top-N cumulative liquidity
    topn_bid_notional = sum(price * qty for price, qty in bids[:top_n])
    topn_ask_notional = sum(price * qty for price, qty in asks[:top_n])

    # Band-based liquidity: sum notional within X bps of mid
    band_bid_notional: dict[int, float] = {}
    for band in band_bps:
        if band <= 0:
            raise ValueError("band_bps values must be positive")
        # Threshold is mid price minus band percentage
        threshold = mid * (1 - band / 10_000)
        band_bid_notional[band] = sum(
            price * qty for price, qty in bids if price >= threshold
        )

    # Slippage estimation for emergency unwind
    unwind_slippage_bps = compute_unwind_slippage_bps(
        bids, mid, stress_notional=stress_notional
    )

    return DepthSnapshotMetrics(
        best_bid_notional=best_bid_notional,
        best_ask_notional=best_ask_notional,
        topn_bid_notional=topn_bid_notional,
        topn_ask_notional=topn_ask_notional,
        band_bid_notional=band_bid_notional,
        unwind_slippage_bps=unwind_slippage_bps,
    )


def compute_unwind_slippage_bps(
    bids: Sequence[tuple[float, float]],
    mid_price: float,
    *,
    stress_notional: float,
) -> float | None:
    """
    Simulate emergency position unwind and calculate expected slippage.

    Walks through bid levels to simulate selling `stress_notional` worth
    of the base asset, computing the volume-weighted average execution
    price. Slippage is the deviation from mid price.

    Algorithm:
        1. Walk through bid levels from best to worst
        2. Fill as much as possible at each level until target reached
        3. Calculate VWAP = total_quote_spent / total_base_sold
        4. Slippage = (mid - VWAP) / mid × 10,000 bps

    Args:
        bids: Parsed bid levels as (price, quantity) tuples, sorted descending.
        mid_price: Current mid price for slippage calculation.
        stress_notional: Target notional value to sell (in quote currency).

    Returns:
        Slippage in basis points, or None if book too thin to fill the order.

    Raises:
        ValueError: If mid_price or stress_notional not positive.

    Example:
        >>> bids = [(100.0, 10.0), (99.0, 20.0), (98.0, 30.0)]
        >>> compute_unwind_slippage_bps(bids, 100.5, stress_notional=1500)
        150.0  # ~1.5% slippage
    """
    if mid_price <= 0:
        raise ValueError("Mid price must be positive")
    if stress_notional <= 0:
        raise ValueError("stress_notional must be positive")
    total_quote = 0.0  # Total quote currency received
    total_base = 0.0   # Total base asset sold
    remaining = stress_notional  # Remaining notional to fill

    # Walk through bid levels, filling until target reached
    for price, qty in bids:
        level_notional = price * qty
        if level_notional >= remaining:
            # Partial fill at this level completes the order
            fill_qty = remaining / price
            total_quote += remaining
            total_base += fill_qty
            remaining = 0.0
            break
        # Full fill at this level, continue to next
        total_quote += level_notional
        total_base += qty
        remaining -= level_notional

    # Return None if couldn't fill the entire order
    if remaining > 0 or total_base <= 0:
        return None

    # Calculate VWAP and slippage from mid
    avg_price = total_quote / total_base
    return (mid_price - avg_price) / mid_price * 10_000


def aggregate_depth_metrics(
    snapshots: Sequence[DepthSnapshotMetrics],
    *,
    band_bps: Iterable[int],
) -> dict[str, object]:
    """
    Aggregate multiple depth snapshots into summary statistics.

    Computes median values for liquidity metrics and P90 for slippage
    across all collected snapshots. This provides robust estimates
    less sensitive to temporary order book fluctuations.

    Args:
        snapshots: Sequence of DepthSnapshotMetrics from multiple samples.
        band_bps: Basis point bands to aggregate (must match snapshot bands).

    Returns:
        Dictionary with aggregated metrics:
        - best_bid_notional_median: Median best bid liquidity
        - best_ask_notional_median: Median best ask liquidity
        - topn_bid_notional_median: Median top-N bid liquidity
        - topn_ask_notional_median: Median top-N ask liquidity
        - band_bid_notional_median: Dict of band -> median notional
        - unwind_slippage_p90_bps: 90th percentile slippage (worst case)

    Note:
        Returns dict with None values if snapshots is empty.
        Slippage P90 excludes snapshots where slippage couldn't be computed.
    """
    if not snapshots:
        return {
            "best_bid_notional_median": None,
            "best_ask_notional_median": None,
            "topn_bid_notional_median": None,
            "topn_ask_notional_median": None,
            "band_bid_notional_median": {},
            "unwind_slippage_p90_bps": None,
        }

    # Collect values from all snapshots
    best_bid = [snap.best_bid_notional for snap in snapshots]
    best_ask = [snap.best_ask_notional for snap in snapshots]
    topn_bid = [snap.topn_bid_notional for snap in snapshots]
    topn_ask = [snap.topn_ask_notional for snap in snapshots]
    # Only include valid slippage values (None indicates insufficient liquidity)
    slippage = [snap.unwind_slippage_bps for snap in snapshots if snap.unwind_slippage_bps is not None]

    # Compute median band notional for each band
    band_medians: dict[int, float] = {}
    for band in band_bps:
        band_values = [snap.band_bid_notional.get(band, 0.0) for snap in snapshots]
        band_medians[band] = statistics.median(band_values)

    # P90 slippage represents worst-case (90th percentile) scenario
    slippage_p90 = None
    if slippage:
        slippage_sorted = sorted(slippage)
        slippage_p90 = _percentile(slippage_sorted, 0.90)

    return {
        "best_bid_notional_median": statistics.median(best_bid),
        "best_ask_notional_median": statistics.median(best_ask),
        "topn_bid_notional_median": statistics.median(topn_bid),
        "topn_ask_notional_median": statistics.median(topn_ask),
        "band_bid_notional_median": band_medians,
        "unwind_slippage_p90_bps": slippage_p90,
    }
