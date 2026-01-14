from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SpreadSampleResult:
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
    mid = (bid + ask) / 2
    if mid <= 0:
        raise ValueError("Mid price must be positive")
    return (ask - bid) / mid * 10_000
