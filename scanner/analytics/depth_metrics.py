from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class DepthSnapshotMetrics:
    best_bid_notional: float
    best_ask_notional: float
    topn_bid_notional: float
    topn_ask_notional: float
    band_bid_notional: dict[int, float]
    unwind_slippage_bps: float | None


def _percentile(sorted_values: Sequence[float], percentile: float) -> float:
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
    bids = _parse_levels(bids_raw)
    asks = _parse_levels(asks_raw)
    if not bids or not asks:
        raise ValueError("Empty book")
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if stress_notional <= 0:
        raise ValueError("stress_notional must be positive")

    best_bid_price, best_bid_qty = bids[0]
    best_ask_price, best_ask_qty = asks[0]
    mid = (best_bid_price + best_ask_price) / 2
    if mid <= 0:
        raise ValueError("Mid price must be positive")

    best_bid_notional = best_bid_price * best_bid_qty
    best_ask_notional = best_ask_price * best_ask_qty

    topn_bid_notional = sum(price * qty for price, qty in bids[:top_n])
    topn_ask_notional = sum(price * qty for price, qty in asks[:top_n])

    band_bid_notional: dict[int, float] = {}
    for band in band_bps:
        if band <= 0:
            raise ValueError("band_bps values must be positive")
        threshold = mid * (1 - band / 10_000)
        band_bid_notional[band] = sum(
            price * qty for price, qty in bids if price >= threshold
        )

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
    if mid_price <= 0:
        raise ValueError("Mid price must be positive")
    if stress_notional <= 0:
        raise ValueError("stress_notional must be positive")
    total_quote = 0.0
    total_base = 0.0
    remaining = stress_notional

    for price, qty in bids:
        level_notional = price * qty
        if level_notional >= remaining:
            fill_qty = remaining / price
            total_quote += remaining
            total_base += fill_qty
            remaining = 0.0
            break
        total_quote += level_notional
        total_base += qty
        remaining -= level_notional

    if remaining > 0 or total_base <= 0:
        return None

    avg_price = total_quote / total_base
    return (mid_price - avg_price) / mid_price * 10_000


def aggregate_depth_metrics(
    snapshots: Sequence[DepthSnapshotMetrics],
    *,
    band_bps: Iterable[int],
) -> dict[str, object]:
    if not snapshots:
        return {
            "best_bid_notional_median": None,
            "best_ask_notional_median": None,
            "topn_bid_notional_median": None,
            "topn_ask_notional_median": None,
            "band_bid_notional_median": None,
            "unwind_slippage_p90_bps": None,
        }

    best_bid = [snap.best_bid_notional for snap in snapshots]
    best_ask = [snap.best_ask_notional for snap in snapshots]
    topn_bid = [snap.topn_bid_notional for snap in snapshots]
    topn_ask = [snap.topn_ask_notional for snap in snapshots]
    slippage = [snap.unwind_slippage_bps for snap in snapshots if snap.unwind_slippage_bps is not None]

    band_medians: dict[int, float] = {}
    for band in band_bps:
        band_values = [snap.band_bid_notional.get(band, 0.0) for snap in snapshots]
        band_medians[band] = statistics.median(band_values)

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
