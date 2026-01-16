from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Sequence

from scanner.models.spread import compute_spread_bps

MIN_SAMPLE_COUNT = 3


@dataclass(frozen=True)
class SpreadSample:
    symbol: str
    bid: float
    ask: float


@dataclass(frozen=True)
class SpreadStats:
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


def compute_spread_stats(samples: Sequence[SpreadSample]) -> SpreadStats:
    if not samples:
        raise ValueError("No samples provided for spread stats")

    symbol = next((sample.symbol for sample in samples if sample.symbol), None)
    spreads: list[float] = []
    invalid_quotes = 0

    for sample in samples:
        try:
            spread_bps = compute_spread_bps(sample.bid, sample.ask)
        except ValueError:
            invalid_quotes += 1
            continue
        spreads.append(spread_bps)

    sample_count = len(samples)
    valid_samples = len(spreads)
    uptime = valid_samples / sample_count if sample_count else 0.0
    insufficient_samples = valid_samples < MIN_SAMPLE_COUNT

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
