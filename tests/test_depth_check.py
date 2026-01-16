from pathlib import Path

import pytest

from scanner.config import AppConfig
from scanner.mexc.errors import RateLimitedError
from scanner.pipeline.depth_check import _select_candidates, run_depth_check
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
        edge_with_unwind_bps=0.0,
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
    candidates = [_score_result(f"S{i:03d}", float(i), False) for i in range(300)]

    selected, pass_spread_total = _select_candidates(candidates, limit=50)

    assert pass_spread_total == 0
    assert len(selected) == 50
    assert selected[0] == "S299"


def test_select_candidates_limits_strings() -> None:
    candidates = [f"S{i:02d}" for i in range(20)]

    selected, pass_spread_total = _select_candidates(candidates, limit=10)

    assert pass_spread_total == 0
    assert selected == candidates[:10]
