from pathlib import Path

from scanner.analytics.scoring import ScoreResult, score_symbol
from scanner.analytics.spread_stats import SpreadStats
from scanner.config import AppConfig
from scanner.io.depth_export import export_summary_enriched
from scanner.models.depth import DepthSymbolMetrics


def test_score_symbol_edge_formulas() -> None:
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
    )
    cfg = AppConfig.model_validate(
        {
            "fees": {"maker_bps": 2.0, "taker_bps": 4.0},
            "thresholds": {"slippage_buffer_bps": 2.0},
        }
    )

    result = score_symbol(stats, cfg)

    assert result.edge_mm_bps == 4.0
    assert result.edge_with_unwind_bps == 2.0


def test_export_summary_enriched_applies_edge_min(tmp_path: Path) -> None:
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
    )
    score = ScoreResult(
        symbol="BTCUSDT",
        spread_stats=stats,
        edge_mm_bps=1.0,
        edge_with_unwind_bps=1.0,
        net_edge_bps=1.0,
        pass_spread=True,
        score=90.0,
        fail_reasons=(),
    )
    depth = DepthSymbolMetrics(
        symbol="BTCUSDT",
        sample_count=1,
        valid_samples=1,
        empty_book_count=0,
        invalid_book_count=0,
        symbol_unavailable_count=0,
        best_bid_notional_median=100.0,
        best_ask_notional_median=110.0,
        topn_bid_notional_median=150.0,
        topn_ask_notional_median=160.0,
        band_bid_notional_median={5: 200.0},
        unwind_slippage_p90_bps=25.0,
        uptime=1.0,
        pass_depth=True,
        fail_reasons=(),
    )

    summary_path = export_summary_enriched(
        tmp_path,
        [score],
        [depth],
        band_bps=[5],
        edge_min_bps=3.0,
    )

    rows = summary_path.read_text(encoding="utf-8").splitlines()[1:]
    pass_total = rows[0].split(",")[4]
    assert pass_total == "False"
