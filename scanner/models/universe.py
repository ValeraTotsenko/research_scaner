"""
Data models for universe filtering results.

This module defines the dataclasses used to represent the output
of the universe filtering stage, including accepted symbols,
rejected symbols with reasons, and aggregate statistics.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UniverseReject:
    """
    Record of a rejected trading symbol with rejection reason.

    Attributes:
        symbol: Trading pair identifier that was rejected.
        reason: Code explaining why symbol was filtered out
            (e.g., "wrong_quote_asset", "low_volume", "low_trades").
    """
    symbol: str
    reason: str


@dataclass(frozen=True)
class UniverseStats:
    """
    Aggregate statistics from universe filtering.

    Attributes:
        total: Total symbols evaluated from exchange.
        kept: Symbols passing all filters (in final universe).
        rejected: Symbols filtered out (total - kept).
    """
    total: int
    kept: int
    rejected: int


@dataclass(frozen=True)
class UniverseResult:
    """
    Complete result from the universe filtering stage.

    Attributes:
        symbols: List of symbol strings passing all filters.
        rejects: List of UniverseReject records for filtered symbols.
        stats: Aggregate counts (total, kept, rejected).
        source_flags: Dict of symbol -> flags from exchange info
            (e.g., isMarginOpen, isSpotTradingAllowed).
    """
    symbols: list[str]
    rejects: list[UniverseReject]
    stats: UniverseStats
    source_flags: dict[str, dict[str, object]]
