from pathlib import Path

import pytest

from scanner.config import AppConfig
from scanner.mexc.errors import RateLimitedError
from scanner.pipeline.depth_check import run_depth_check


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
    return AppConfig().model_copy(
        update={
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
