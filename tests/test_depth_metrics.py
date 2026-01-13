import pytest

from scanner.analytics.depth_metrics import (
    compute_snapshot_metrics,
    compute_unwind_slippage_bps,
)


def test_best_level_notional() -> None:
    bids = [["100", "2"]]
    asks = [["101", "1"]]
    metrics = compute_snapshot_metrics(
        bids,
        asks,
        top_n=1,
        band_bps=[10],
        stress_notional=150,
    )

    assert metrics.best_bid_notional == 200
    assert metrics.best_ask_notional == 101


def test_band_depth() -> None:
    bids = [["100", "2"], ["99.9", "1"]]
    asks = [["101", "1"]]
    metrics = compute_snapshot_metrics(
        bids,
        asks,
        top_n=2,
        band_bps=[10],
        stress_notional=100,
    )

    assert metrics.band_bid_notional[10] == pytest.approx(200)


def test_unwind_slippage() -> None:
    bids = [(100.0, 1.0), (99.0, 1.0)]
    slippage = compute_unwind_slippage_bps(bids, 100.5, stress_notional=100.0)
    assert slippage == pytest.approx(49.7512, rel=1e-4)


def test_unwind_slippage_insufficient_depth() -> None:
    bids = [(100.0, 0.1)]
    slippage = compute_unwind_slippage_bps(bids, 100.0, stress_notional=100.0)
    assert slippage is None
