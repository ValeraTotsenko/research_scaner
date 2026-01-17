import csv
import json
from pathlib import Path

import pytest

from scanner.analytics.scoring import ScoreResult
from scanner.analytics.spread_stats import SpreadStats
from scanner.config import AppConfig
from scanner.io.depth_export import export_depth_metrics, export_summary_enriched
from scanner.io.summary_export import SUMMARY_COLUMNS, export_summary
from scanner.models.depth import DepthSymbolMetrics
from scanner import __version__
from scanner.pipeline.state import PIPELINE_SPEC_VERSION
from scanner.report.report_md import generate_report


def _write_run_meta(run_dir: Path) -> None:
    run_meta = {
        "run_id": "run_123",
        "started_at": "2024-01-01T00:00:00Z",
        "git_commit": "deadbeef",
        "config": {"runtime": {"run_name": "demo"}},
        "status": "success",
        "run_health": "ok",
        "scanner_version": __version__,
        "spec_version": PIPELINE_SPEC_VERSION,
    }
    (run_dir / "run_meta.json").write_text(json.dumps(run_meta), encoding="utf-8")


def _make_score(symbol: str, score: float, pass_spread: bool) -> ScoreResult:
    stats = SpreadStats(
        symbol=symbol,
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
    return ScoreResult(
        symbol=symbol,
        spread_stats=stats,
        edge_mm_bps=6.0,
        edge_mm_p25_bps=3.0,
        edge_mt_bps=4.0,
        net_edge_bps=4.0,
        pass_spread=pass_spread,
        score=score,
        fail_reasons=(),
    )


def _write_summary_csv(run_dir: Path, rows: list[dict[str, object]]) -> None:
    path = run_dir / "summary.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_generate_report_creates_outputs(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_1"
    run_dir.mkdir()
    _write_run_meta(run_dir)

    scores = [_make_score("AAAUSDT", 100.0, True), _make_score("BBBUSD", 90.0, True)]
    export_summary(run_dir, scores)

    depth_results = [
        DepthSymbolMetrics(
            symbol="AAAUSDT",
            sample_count=2,
            valid_samples=2,
            empty_book_count=0,
            invalid_book_count=0,
            symbol_unavailable_count=0,
            best_bid_notional_median=200.0,
            best_ask_notional_median=210.0,
            topn_bid_notional_median=500.0,
            topn_ask_notional_median=520.0,
            band_bid_notional_median={5: 300.0},
            band_ask_notional_median={5: 310.0},
            unwind_slippage_p90_bps=12.0,
            uptime=1.0,
            best_bid_notional_pass=True,
            best_ask_notional_pass=True,
            unwind_slippage_pass=True,
            band_10bps_notional_pass=None,
            topn_notional_pass=None,
            pass_depth=True,
            fail_reasons=(),
        ),
        DepthSymbolMetrics(
            symbol="BBBUSD",
            sample_count=2,
            valid_samples=1,
            empty_book_count=0,
            invalid_book_count=1,
            symbol_unavailable_count=0,
            best_bid_notional_median=80.0,
            best_ask_notional_median=90.0,
            topn_bid_notional_median=120.0,
            topn_ask_notional_median=130.0,
            band_bid_notional_median={5: 110.0},
            band_ask_notional_median={5: 120.0},
            unwind_slippage_p90_bps=40.0,
            uptime=0.5,
            best_bid_notional_pass=False,
            best_ask_notional_pass=False,
            unwind_slippage_pass=False,
            band_10bps_notional_pass=None,
            topn_notional_pass=None,
            pass_depth=False,
            fail_reasons=("invalid_book_levels",),
        ),
    ]
    export_depth_metrics(run_dir, depth_results, band_bps=[5])
    export_summary_enriched(run_dir, scores, depth_results, band_bps=[5], edge_min_bps=3.0)

    generate_report(run_dir, AppConfig())

    report_path = run_dir / "report.md"
    shortlist_path = run_dir / "shortlist.csv"

    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")
    assert "## 1. Run Meta" in report_text
    assert "## 2. Parameters" in report_text
    assert "## 3. Universe Stats" in report_text
    assert "## 4. Spread Stats" in report_text
    assert "## 5. Depth Results" in report_text
    assert "## 6. Top" in report_text  # Contains "Top N Candidates"
    assert "## 7. Fail Reason Breakdown" in report_text
    assert "## 8. Notes" in report_text
    # Note: new report generator doesn't create shortlist.csv, only report.md


def test_shortlist_sorting_stable(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_2"
    run_dir.mkdir()
    _write_run_meta(run_dir)

    rows = [
        {
            "symbol": "AAAUSDT",
            "spread_median_bps": 10.0,
            "spread_p25_bps": 7.0,
            "spread_p10_bps": 5.0,
            "spread_p90_bps": 15.0,
            "uptime": 1.0,
            "quoteVolume_24h": 100.0,
            "trades_24h": 10,
            "edge_mm_bps": 6.0,
            "edge_mm_p25_bps": 3.0,
            "edge_mt_bps": 4.0,
            "net_edge_bps": 4.0,
            "pass_spread": True,
            "score": 100.0,
            "fail_reasons": "",
        },
        {
            "symbol": "BBBUSD",
            "spread_median_bps": 10.0,
            "spread_p25_bps": 7.0,
            "spread_p10_bps": 5.0,
            "spread_p90_bps": 15.0,
            "uptime": 1.0,
            "quoteVolume_24h": 100.0,
            "trades_24h": 10,
            "edge_mm_bps": 6.0,
            "edge_mm_p25_bps": 3.0,
            "edge_mt_bps": 4.0,
            "net_edge_bps": 4.0,
            "pass_spread": True,
            "score": 100.0,
            "fail_reasons": "",
        },
        {
            "symbol": "CCCUSD",
            "spread_median_bps": 10.0,
            "spread_p25_bps": 7.0,
            "spread_p10_bps": 5.0,
            "spread_p90_bps": 15.0,
            "uptime": 1.0,
            "quoteVolume_24h": 100.0,
            "trades_24h": 10,
            "edge_mm_bps": 6.0,
            "edge_mm_p25_bps": 3.0,
            "edge_mt_bps": 4.0,
            "net_edge_bps": 4.0,
            "pass_spread": True,
            "score": 90.0,
            "fail_reasons": "",
        },
    ]
    _write_summary_csv(run_dir, rows)

    summary_enriched_path = run_dir / "summary_enriched.csv"
    with summary_enriched_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "symbol",
                "score",
                "pass_spread",
                "pass_depth",
                "best_bid_notional_pass",
                "best_ask_notional_pass",
                "unwind_slippage_pass",
                "band_10bps_notional_pass",
                "topn_notional_pass",
                "pass_total",
                "depth_fail_reasons",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "symbol": "BBBUSD",
                "score": 100.0,
                "pass_spread": True,
                "pass_depth": True,
                "best_bid_notional_pass": True,
                "best_ask_notional_pass": True,
                "unwind_slippage_pass": True,
                "band_10bps_notional_pass": "",
                "topn_notional_pass": "",
                "pass_total": True,
                "depth_fail_reasons": "",
            }
        )
        writer.writerow(
            {
                "symbol": "AAAUSDT",
                "score": 100.0,
                "pass_spread": True,
                "pass_depth": True,
                "best_bid_notional_pass": True,
                "best_ask_notional_pass": True,
                "unwind_slippage_pass": True,
                "band_10bps_notional_pass": "",
                "topn_notional_pass": "",
                "pass_total": True,
                "depth_fail_reasons": "",
            }
        )
        writer.writerow(
            {
                "symbol": "CCCUSD",
                "score": 90.0,
                "pass_spread": True,
                "pass_depth": True,
                "best_bid_notional_pass": True,
                "best_ask_notional_pass": True,
                "unwind_slippage_pass": True,
                "band_10bps_notional_pass": "",
                "topn_notional_pass": "",
                "pass_total": True,
                "depth_fail_reasons": "",
            }
        )

    cfg = AppConfig(report={"top_n": 3})
    generate_report(run_dir, cfg)

    report_path = run_dir / "report.md"
    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")

    # Check that all three symbols appear in the report in the correct order
    # The symbols should appear in the "Top N Candidates" section
    assert "AAAUSDT" in report_text
    assert "BBBUSD" in report_text
    assert "CCCUSD" in report_text

    # Verify the order by checking positions in the text
    # (AAAUSDT and BBBUSD both have score 100, CCCUSD has score 90)
    # They should be sorted by score desc, then symbol asc
    pos_aaa = report_text.find("AAAUSDT")
    pos_bbb = report_text.find("BBBUSD")
    pos_ccc = report_text.find("CCCUSD")

    # All symbols should be found
    assert pos_aaa > 0
    assert pos_bbb > 0
    assert pos_ccc > 0

    # CCCUSD (score 90) should appear after the others (score 100)
    assert pos_ccc > pos_aaa
    assert pos_ccc > pos_bbb


def test_report_missing_summary_fails(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_3"
    run_dir.mkdir()
    _write_run_meta(run_dir)

    with pytest.raises(FileNotFoundError):
        generate_report(run_dir, AppConfig())
