from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UniverseReject:
    symbol: str
    reason: str


@dataclass(frozen=True)
class UniverseStats:
    total: int
    kept: int
    rejected: int


@dataclass(frozen=True)
class UniverseResult:
    symbols: list[str]
    rejects: list[UniverseReject]
    stats: UniverseStats
