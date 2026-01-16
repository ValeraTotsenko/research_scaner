import pytest

from scanner.analytics.spread_stats import SpreadSample, compute_spread_stats


def _sample_for_spread(spread_bps: float) -> SpreadSample:
    half_spread = spread_bps / 200
    return SpreadSample(symbol="BTCUSDT", bid=100 - half_spread, ask=100 + half_spread)


def test_compute_spread_stats_quantiles() -> None:
    samples = [_sample_for_spread(value) for value in [10, 20, 30, 40, 50]]
    stats = compute_spread_stats(samples)

    assert stats.spread_median_bps == pytest.approx(30.0)
    assert stats.spread_p10_bps == pytest.approx(14.0)
    assert stats.spread_p25_bps == pytest.approx(20.0)
    assert stats.spread_p90_bps == pytest.approx(46.0)
    assert stats.uptime == pytest.approx(1.0)


def test_compute_spread_stats_tracks_invalid_quotes() -> None:
    # For a quote to be invalid, mid = (bid + ask) / 2 must be <= 0
    # Using bid=-1.0, ask=0.5 gives mid = -0.25 <= 0 -> invalid
    samples = [_sample_for_spread(10), SpreadSample(symbol="BTCUSDT", bid=-1.0, ask=0.5)]
    stats = compute_spread_stats(samples)

    assert stats.invalid_quotes == 1
    assert stats.valid_samples == 1
    assert stats.uptime == pytest.approx(0.5)


def test_empty_samples_raise() -> None:
    with pytest.raises(ValueError, match="No samples provided"):
        compute_spread_stats([])
