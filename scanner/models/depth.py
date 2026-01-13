from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DepthSymbolMetrics:
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
    band_bid_notional_median: dict[int, float] | None
    unwind_slippage_p90_bps: float | None
    uptime: float
    pass_depth: bool
    fail_reasons: tuple[str, ...]


@dataclass(frozen=True)
class DepthCheckResult:
    target_ticks: int
    ticks_success: int
    ticks_fail: int
    symbols: tuple[DepthSymbolMetrics, ...]
    depth_requests_total: int
    depth_fail_total: int
    depth_symbols_pass_total: int
