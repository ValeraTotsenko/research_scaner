from pathlib import Path

from scanner.analytics.scoring import ScoreResult
from scanner.analytics.spread_stats import SpreadStats
from scanner.io.summary_export import SUMMARY_COLUMNS, export_summary


def test_summary_export_columns(tmp_path: Path) -> None:
    stats = SpreadStats(
        symbol="BTCUSDT",
        sample_count=3,
        valid_samples=3,
        invalid_quotes=0,
        spread_median_bps=10.0,
        spread_p10_bps=5.0,
        spread_p25_bps=7.0,
        spread_p90_bps=15.0,
        uptime=1.0,
        insufficient_samples=False,
        quote_volume_24h=100.0,
        trades_24h=10,
    )
    result = ScoreResult(
        symbol="BTCUSDT",
        spread_stats=stats,
        edge_mm_bps=6.0,
        edge_mm_p25_bps=3.0,
        edge_mt_bps=4.0,
        net_edge_bps=4.0,
        pass_spread=True,
        score=120.0,
        fail_reasons=(),
    )

    paths = export_summary(tmp_path, [result])

    header = paths.csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == SUMMARY_COLUMNS
