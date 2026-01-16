from pathlib import Path

from scanner.analytics.scoring import ScoreResult
from scanner.analytics.spread_stats import SpreadStats
from scanner.io.depth_export import export_depth_metrics, export_summary_enriched
from scanner.models.depth import DepthSymbolMetrics


def _spread_stats(symbol: str) -> SpreadStats:
    return SpreadStats(
        symbol=symbol,
        sample_count=5,
        valid_samples=5,
        invalid_quotes=0,
        spread_median_bps=10.0,
        spread_p10_bps=5.0,
        spread_p25_bps=7.0,
        spread_p90_bps=15.0,
        uptime=1.0,
        insufficient_samples=False,
        quote_volume_24h=100000.0,
        trades_24h=1000,
    )


def _score(symbol: str) -> ScoreResult:
    return ScoreResult(
        symbol=symbol,
        spread_stats=_spread_stats(symbol),
        edge_mm_bps=6.0,
        edge_with_unwind_bps=4.0,
        net_edge_bps=5.0,
        pass_spread=True,
        score=90.0,
        fail_reasons=(),
    )


def test_export_depth_outputs(tmp_path: Path) -> None:
    results = [
        DepthSymbolMetrics(
            symbol="BTCUSDT",
            sample_count=2,
            valid_samples=2,
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
            best_bid_notional_pass=True,
            best_ask_notional_pass=True,
            unwind_slippage_pass=True,
            band_10bps_notional_pass=None,
            topn_notional_pass=None,
            pass_depth=True,
            fail_reasons=(),
        )
    ]

    csv_path = export_depth_metrics(tmp_path, results, band_bps=[5])
    assert csv_path.exists()

    summary_path = export_summary_enriched(
        tmp_path,
        [_score("BTCUSDT")],
        results,
        band_bps=[5],
        edge_min_bps=3.0,
    )
    content = summary_path.read_text(encoding="utf-8")
    assert "pass_total" in content


def test_export_summary_enriched_handles_missing_depth(tmp_path: Path) -> None:
    summary_path = export_summary_enriched(
        tmp_path,
        [_score("BTCUSDT")],
        [],
        band_bps=[5],
        edge_min_bps=3.0,
    )
    content = summary_path.read_text(encoding="utf-8")
    assert "no_depth_data" in content


def test_export_summary_enriched_handles_missing_band_metrics(tmp_path: Path) -> None:
    results = [
        DepthSymbolMetrics(
            symbol="BTCUSDT",
            sample_count=1,
            valid_samples=1,
            empty_book_count=0,
            invalid_book_count=0,
            symbol_unavailable_count=0,
            best_bid_notional_median=None,
            best_ask_notional_median=None,
            topn_bid_notional_median=None,
            topn_ask_notional_median=None,
            band_bid_notional_median=None,
            unwind_slippage_p90_bps=None,
            uptime=0.0,
            best_bid_notional_pass=False,
            best_ask_notional_pass=False,
            unwind_slippage_pass=False,
            band_10bps_notional_pass=None,
            topn_notional_pass=None,
            pass_depth=False,
            fail_reasons=("no_valid_samples",),
        )
    ]
    summary_path = export_summary_enriched(
        tmp_path,
        [_score("BTCUSDT")],
        results,
        band_bps=[5],
        edge_min_bps=3.0,
    )
    assert summary_path.exists()
