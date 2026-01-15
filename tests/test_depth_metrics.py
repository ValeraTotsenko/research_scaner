from scanner.analytics.depth_metrics import aggregate_depth_metrics


def test_aggregate_depth_metrics_empty_snapshots_returns_empty_bands() -> None:
    metrics = aggregate_depth_metrics([], band_bps=[5, 10])
    assert metrics["band_bid_notional_median"] == {}
