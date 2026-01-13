import gzip
from pathlib import Path

import pytest

from scanner.config import RawSamplingConfig, SamplingConfig, SpreadSamplingConfig
from scanner.io.raw_writer import create_raw_bookticker_writer
from scanner.mexc.errors import RateLimitedError
from scanner.models.spread import compute_spread_bps
from scanner.pipeline.spread_sampling import run_spread_sampling


class FakeBookTickerClient:
    def __init__(self, payloads: list[list[dict] | Exception]) -> None:
        self._payloads = payloads

    def get_book_ticker(self) -> list[dict]:
        payload = self._payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload

    def get_book_ticker_symbol(self, symbol: str) -> dict:
        raise AssertionError("Per-symbol fallback not expected")


def test_compute_spread_bps() -> None:
    spread = compute_spread_bps(100.0, 101.0)
    assert spread == pytest.approx(99.502487, rel=1e-6)


def test_filtering_symbols(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    client = FakeBookTickerClient(
        [
            [
                {"symbol": "BTCUSDT", "bidPrice": "100", "askPrice": "101"},
                {"symbol": "ETHUSDT", "bidPrice": "200", "askPrice": "201"},
            ]
        ]
    )
    cfg = SamplingConfig(
        spread=SpreadSamplingConfig(duration_s=1, interval_s=1, min_uptime=0.9),
        raw=RawSamplingConfig(enabled=False, gzip=True),
    )
    result = run_spread_sampling(client, ["BTCUSDT"], cfg, tmp_path)

    assert result.ticks_success == 1
    assert result.invalid_quotes == 0
    assert result.missing_quotes == 0


def test_raw_writer_gzip(tmp_path: Path) -> None:
    writer = create_raw_bookticker_writer(tmp_path, gzip_enabled=True)
    with writer:
        writer.write({"ts": "2024-01-01T00:00:00Z", "symbol": "BTCUSDT", "bid": "1", "ask": "2"})

    assert writer.path.suffixes[-2:] == [".jsonl", ".gz"]
    with gzip.open(writer.path, "rt", encoding="utf-8") as handle:
        content = handle.read().strip()
    assert '"symbol": "BTCUSDT"' in content


def test_rate_limit_degrades_uptime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    client = FakeBookTickerClient(
        [
            RateLimitedError("rate limit", status_code=429),
            [{"symbol": "BTCUSDT", "bidPrice": "10", "askPrice": "11"}],
        ]
    )
    cfg = SamplingConfig(
        spread=SpreadSamplingConfig(duration_s=2, interval_s=1, min_uptime=0.9),
        raw=RawSamplingConfig(enabled=False, gzip=True),
    )
    result = run_spread_sampling(client, ["BTCUSDT"], cfg, tmp_path)

    assert result.uptime == pytest.approx(0.5)
    assert result.low_quality is True
