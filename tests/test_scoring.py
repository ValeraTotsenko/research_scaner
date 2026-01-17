from scanner.analytics.scoring import score_symbol
from scanner.analytics.spread_stats import SpreadStats
from scanner.config import AppConfig, SpreadThresholdsConfig, ThresholdsConfig


def test_score_symbol_pass_spread() -> None:
    stats = SpreadStats(
        symbol="BTCUSDT",
        sample_count=5,
        valid_samples=5,
        invalid_quotes=0,
        spread_median_bps=20.0,
        spread_p10_bps=15.0,
        spread_p25_bps=18.0,
        spread_p90_bps=30.0,
        uptime=0.95,
        insufficient_samples=False,
        quote_volume_24h=1_000_000.0,
        trades_24h=500,
        missing_24h_stats=False,
    )
    cfg = AppConfig(
        thresholds=ThresholdsConfig(
            spread=SpreadThresholdsConfig(
                median_min_bps=8.0,
                median_max_bps=25.0,
                p90_min_bps=0.0,
                p90_max_bps=35.0,
            ),
            uptime_min=0.9,
        )
    )

    result = score_symbol(stats, cfg)

    assert result.pass_spread is True
    assert "missing_24h_stats" not in result.fail_reasons


def test_score_symbol_flags_fail_reasons() -> None:
    """Test that scoring correctly identifies fail reasons for spread criteria.

    Note: missing_24h_stats is NOT added to fail_reasons per AD-101.
    It's an informational flag, not a scoring criterion.
    """
    stats = SpreadStats(
        symbol="ETHUSDT",
        sample_count=5,
        valid_samples=5,
        invalid_quotes=1,
        spread_median_bps=30.0,
        spread_p10_bps=10.0,
        spread_p25_bps=15.0,
        spread_p90_bps=70.0,
        uptime=0.5,
        insufficient_samples=False,
        quote_volume_24h=None,
        trades_24h=None,
        missing_24h_stats=True,
    )
    cfg = AppConfig(
        thresholds=ThresholdsConfig(
            spread=SpreadThresholdsConfig(
                median_min_bps=8.0,
                median_max_bps=25.0,
                p90_min_bps=0.0,
                p90_max_bps=60.0,
            ),
            uptime_min=0.9,
        )
    )

    result = score_symbol(stats, cfg)

    assert result.pass_spread is False
    assert "invalid_quotes" in result.fail_reasons
    assert "low_uptime" in result.fail_reasons
    assert "spread_median_high" in result.fail_reasons
    assert "spread_p90_high" in result.fail_reasons
    # Per AD-101: missing_24h_stats is informational only, NOT a fail reason
    assert "missing_24h_stats" not in result.fail_reasons


def test_score_symbol_rejects_low_spreads() -> None:
    stats = SpreadStats(
        symbol="SOLUSDT",
        sample_count=5,
        valid_samples=5,
        invalid_quotes=0,
        spread_median_bps=5.0,
        spread_p10_bps=3.0,
        spread_p25_bps=4.0,
        spread_p90_bps=2.0,
        uptime=0.95,
        insufficient_samples=False,
        quote_volume_24h=500_000.0,
        trades_24h=300,
        missing_24h_stats=False,
    )
    cfg = AppConfig(
        thresholds=ThresholdsConfig(
            spread=SpreadThresholdsConfig(
                median_min_bps=8.0,
                median_max_bps=25.0,
                p90_min_bps=3.0,
                p90_max_bps=60.0,
            ),
            uptime_min=0.9,
        )
    )

    result = score_symbol(stats, cfg)

    assert result.pass_spread is False
    assert "spread_median_low" in result.fail_reasons
    assert "spread_p90_low" in result.fail_reasons
