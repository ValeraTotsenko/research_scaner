from pathlib import Path

import pytest

from scanner.config import AppConfig
from scanner.mexc.errors import RateLimitedError
from scanner.pipeline.depth_check import _evaluate_depth_criteria, _select_candidates, run_depth_check
from scanner.analytics.scoring import ScoreResult
from scanner.analytics.spread_stats import SpreadStats


class FakeDepthClient:
    def __init__(self, payloads: dict[str, list[dict | Exception]]) -> None:
        self._payloads = payloads

    def get_depth(self, symbol: str, limit: int) -> dict:
        _ = limit
        payload = self._payloads[symbol].pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload


def _config(duration_s: int = 1) -> AppConfig:
    return AppConfig.model_validate(
        {
            "sampling": {"depth": {"duration_s": duration_s, "interval_s": 1, "limit": 5}},
            "depth": {"top_n_levels": 1, "band_bps": [5], "stress_notional_usdt": 50.0},
            "thresholds": {"depth": {"best_level_min_notional": 50.0, "unwind_slippage_max_bps": 100.0}},
        }
    )


def test_depth_check_outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    client = FakeDepthClient(
        {
            "BTCUSDT": [
                {"bids": [["100", "1"]], "asks": [["101", "1"]], "lastUpdateId": 1}
            ],
            "ETHUSDT": [
                {"bids": [], "asks": [], "lastUpdateId": 2}
            ],
        }
    )

    result = run_depth_check(client, ["BTCUSDT", "ETHUSDT"], _config(), tmp_path)

    metrics_path = tmp_path / "depth_metrics.csv"
    assert metrics_path.exists()
    assert result.depth_symbols_pass_total == 1
    eth_result = next(item for item in result.symbols if item.symbol == "ETHUSDT")
    assert "empty_book" in eth_result.fail_reasons


def test_rate_limit_degrades(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    client = FakeDepthClient(
        {
            "BTCUSDT": [
                RateLimitedError("rate limit", status_code=429),
                {"bids": [["100", "1"]], "asks": [["101", "1"]], "lastUpdateId": 1},
            ]
        }
    )

    result = run_depth_check(client, ["BTCUSDT"], _config(duration_s=2), tmp_path)

    assert result.depth_fail_total == 1
    assert result.ticks_success == 1


def test_depth_criteria_fail_reasons_unit() -> None:
    cfg = AppConfig.model_validate(
        {
            "depth": {
                "top_n_levels": 1,
                "band_bps": [5, 10],
                "stress_notional_usdt": 50.0,
                "enable_band_checks": True,
                "enable_topN_checks": True,
            },
            "thresholds": {
                "depth": {
                    "best_level_min_notional": 50.0,
                    "unwind_slippage_max_bps": 100.0,
                    "band_10bps_min_notional": 200.0,
                    "topN_min_notional": 300.0,
                }
            },
        }
    )
    base_aggregates = {
        "best_bid_notional_median": 60.0,
        "best_ask_notional_median": 70.0,
        "topn_bid_notional_median": 400.0,
        "topn_ask_notional_median": 400.0,
        "band_bid_notional_median": {10: 250.0},
        "unwind_slippage_p90_bps": 90.0,
    }

    result = _evaluate_depth_criteria(base_aggregates, thresholds=cfg.thresholds.depth, depth_cfg=cfg.depth)
    assert result.fail_reasons == ()

    aggregates = dict(base_aggregates, best_bid_notional_median=40.0)
    result = _evaluate_depth_criteria(aggregates, thresholds=cfg.thresholds.depth, depth_cfg=cfg.depth)
    assert "best_bid_notional_low" in result.fail_reasons

    aggregates = dict(base_aggregates, best_ask_notional_median=40.0)
    result = _evaluate_depth_criteria(aggregates, thresholds=cfg.thresholds.depth, depth_cfg=cfg.depth)
    assert "best_ask_notional_low" in result.fail_reasons

    aggregates = dict(base_aggregates, unwind_slippage_p90_bps=120.0)
    result = _evaluate_depth_criteria(aggregates, thresholds=cfg.thresholds.depth, depth_cfg=cfg.depth)
    assert "unwind_slippage_high" in result.fail_reasons

    aggregates = dict(base_aggregates, band_bid_notional_median={10: 150.0})
    result = _evaluate_depth_criteria(aggregates, thresholds=cfg.thresholds.depth, depth_cfg=cfg.depth)
    assert "band_10bps_notional_low" in result.fail_reasons

    aggregates = dict(base_aggregates, topn_bid_notional_median=200.0)
    result = _evaluate_depth_criteria(aggregates, thresholds=cfg.thresholds.depth, depth_cfg=cfg.depth)
    assert "topn_notional_low" in result.fail_reasons


def test_depth_pass_equals_no_fail_reasons(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    client = FakeDepthClient(
        {
            "BTCUSDT": [
                {"bids": [["100", "1"]], "asks": [["101", "1"]], "lastUpdateId": 1}
            ]
        }
    )

    result = run_depth_check(client, ["BTCUSDT"], _config(), tmp_path)
    depth_result = result.symbols[0]
    assert depth_result.pass_depth == (len(depth_result.fail_reasons) == 0)


def _score_result(symbol: str, score: float, pass_spread: bool) -> ScoreResult:
    stats = SpreadStats(
        symbol=symbol,
        sample_count=3,
        valid_samples=3,
        invalid_quotes=0,
        spread_median_bps=1.0,
        spread_p10_bps=0.5,
        spread_p25_bps=0.75,
        spread_p90_bps=1.5,
        uptime=1.0,
        insufficient_samples=False,
    )
    return ScoreResult(
        symbol=symbol,
        spread_stats=stats,
        edge_mm_bps=0.0,
        edge_mm_p25_bps=0.0,
        edge_mt_bps=0.0,
        net_edge_bps=0.0,
        pass_spread=pass_spread,
        score=score,
        fail_reasons=tuple(),
    )


def test_select_candidates_limits_pass_spread() -> None:
    candidates = [_score_result(f"S{i:04d}", float(i), True) for i in range(1000)] + [
        _score_result("A0999", 999.0, True),
        _score_result("Z0999", 999.0, True),
    ]

    selected, pass_spread_total = _select_candidates(candidates, limit=200)

    assert pass_spread_total == 1002
    assert len(selected) == 200
    assert selected[0] == "A0999"


def test_select_candidates_fallback_to_score() -> None:
    # Changed behavior: if no symbols passed spread, return empty list instead of fallback to all
    candidates = [_score_result(f"S{i:03d}", float(i), False) for i in range(300)]

    selected, pass_spread_total = _select_candidates(candidates, limit=50)

    assert pass_spread_total == 0
    assert len(selected) == 0  # Should return empty list, not fallback to score
    assert selected == []


def test_select_candidates_limits_strings() -> None:
    candidates = [f"S{i:02d}" for i in range(20)]

    selected, pass_spread_total = _select_candidates(candidates, limit=10)

    assert pass_spread_total == 0
    assert selected == candidates[:10]
