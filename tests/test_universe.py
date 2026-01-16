from __future__ import annotations

import pytest

from scanner.config import UniverseConfig
from scanner.pipeline.universe import UniverseBuildError, build_universe


class StubClient:
    def __init__(
        self,
        exchange_info: dict,
        default_symbols: list[str],
        tickers: list[dict],
        book_tickers: list[dict] | None = None,
    ):
        self._exchange_info = exchange_info
        self._default_symbols = default_symbols
        self._tickers = tickers
        self._book_tickers = book_tickers or []

    def get_exchange_info(self) -> dict:
        return self._exchange_info

    def get_default_symbols(self) -> list[str]:
        return self._default_symbols

    def get_ticker_24hr(self) -> list[dict]:
        return self._tickers

    def get_book_ticker(self) -> list[dict]:
        return self._book_tickers


def test_quote_asset_filter() -> None:
    client = StubClient(
        exchange_info={
            "symbols": [
                {"symbol": "AAAUSDT", "quoteAsset": "USDT", "status": "1"},
                {"symbol": "AAABTC", "quoteAsset": "BTC", "status": "1"},
            ]
        },
        default_symbols=["AAAUSDT", "AAABTC"],
        tickers=[
            {"symbol": "AAAUSDT", "quoteVolume": "1000", "count": 10},
            {"symbol": "AAABTC", "quoteVolume": "1000", "count": 10},
        ],
    )
    cfg = UniverseConfig(min_quote_volume_24h=0, min_trades_24h=0)

    result = build_universe(client, cfg)

    assert result.symbols == ["AAAUSDT"]


def test_default_symbols_intersection() -> None:
    client = StubClient(
        exchange_info={
            "symbols": [
                {"symbol": "AAAUSDT", "quoteAsset": "USDT", "status": "1"},
                {"symbol": "BBBUSDT", "quoteAsset": "USDT", "status": "1"},
            ]
        },
        default_symbols=["AAAUSDT"],
        tickers=[
            {"symbol": "AAAUSDT", "quoteVolume": "1000", "count": 10},
            {"symbol": "BBBUSDT", "quoteVolume": "1000", "count": 10},
        ],
    )
    cfg = UniverseConfig(min_quote_volume_24h=0, min_trades_24h=0)

    result = build_universe(client, cfg)

    assert result.symbols == ["AAAUSDT"]
    assert any(reject.symbol == "BBBUSDT" and reject.reason == "not_in_defaultSymbols" for reject in result.rejects)


def test_threshold_filters() -> None:
    client = StubClient(
        exchange_info={
            "symbols": [
                {"symbol": "LOWVOLUSDT", "quoteAsset": "USDT", "status": "1"},
                {"symbol": "LOWTRADESUSDT", "quoteAsset": "USDT", "status": "1"},
                {"symbol": "KEEPUSDT", "quoteAsset": "USDT", "status": "1"},
            ]
        },
        default_symbols=["LOWVOLUSDT", "LOWTRADESUSDT", "KEEPUSDT"],
        tickers=[
            {"symbol": "LOWVOLUSDT", "quoteVolume": "50", "count": 100},
            {"symbol": "LOWTRADESUSDT", "quoteVolume": "5000", "count": 1},
            {"symbol": "KEEPUSDT", "quoteVolume": "5000", "count": 500},
        ],
    )
    cfg = UniverseConfig(min_quote_volume_24h=100, min_trades_24h=10)

    result = build_universe(client, cfg)

    assert result.symbols == ["KEEPUSDT"]
    reasons = {reject.symbol: reject.reason for reject in result.rejects}
    assert reasons["LOWVOLUSDT"] == "low_volume"
    assert reasons["LOWTRADESUSDT"] == "low_trades"


def test_default_symbols_empty_fails() -> None:
    client = StubClient(
        exchange_info={"symbols": [{"symbol": "AAAUSDT", "quoteAsset": "USDT"}]},
        default_symbols=[],
        tickers=[{"symbol": "AAAUSDT", "quoteVolume": "1000", "count": 10}],
    )
    cfg = UniverseConfig(min_quote_volume_24h=0, min_trades_24h=0)

    with pytest.raises(UniverseBuildError):
        build_universe(client, cfg)


def test_quote_volume_estimate_allows_missing_quote_volume() -> None:
    client = StubClient(
        exchange_info={"symbols": [{"symbol": "ESTUSDT", "quoteAsset": "USDT", "status": "1"}]},
        default_symbols=["ESTUSDT"],
        tickers=[
            {
                "symbol": "ESTUSDT",
                "quoteVolume": None,
                "volume": "100",
                "count": None,
            }
        ],
        book_tickers=[{"symbol": "ESTUSDT", "bidPrice": "2.0", "askPrice": "3.0"}],
    )
    cfg = UniverseConfig(min_quote_volume_24h=200, min_trades_24h=10, require_trade_count=False)

    result = build_universe(client, cfg)

    assert result.symbols == ["ESTUSDT"]


def test_missing_trade_count_rejected_when_required() -> None:
    client = StubClient(
        exchange_info={
            "symbols": [
                {"symbol": "KEEPUSDT", "quoteAsset": "USDT", "status": "1"},
                {"symbol": "MISSCOUNTUSDT", "quoteAsset": "USDT", "status": "1"},
            ]
        },
        default_symbols=["KEEPUSDT", "MISSCOUNTUSDT"],
        tickers=[
            {"symbol": "KEEPUSDT", "quoteVolume": "1000", "count": 10},
            {"symbol": "MISSCOUNTUSDT", "quoteVolume": "1000", "count": None},
        ],
    )
    cfg = UniverseConfig(min_quote_volume_24h=0, min_trades_24h=0, require_trade_count=True)

    result = build_universe(client, cfg)

    assert result.symbols == ["KEEPUSDT"]
    assert any(
        reject.symbol == "MISSCOUNTUSDT" and reject.reason == "missing_trade_count"
        for reject in result.rejects
    )


def test_missing_mid_price_rejects_no_volume_data() -> None:
    """When quoteVolume is null and can't estimate (no mid_price), reject with no_volume_data.

    AD-101: This is different from missing_24h_stats which is only for no_row or parse_error.
    """
    client = StubClient(
        exchange_info={
            "symbols": [
                {"symbol": "KEEPUSDT", "quoteAsset": "USDT", "status": "1"},
                {"symbol": "MISSLASTUSDT", "quoteAsset": "USDT", "status": "1"},
            ]
        },
        default_symbols=["KEEPUSDT", "MISSLASTUSDT"],
        tickers=[
            {"symbol": "KEEPUSDT", "quoteVolume": "1000", "count": 10},
            {"symbol": "MISSLASTUSDT", "quoteVolume": None, "volume": "100"},
        ],
        # No book_tickers for MISSLASTUSDT, so mid_price can't be computed
    )
    cfg = UniverseConfig(min_quote_volume_24h=0, min_trades_24h=0)

    result = build_universe(client, cfg)

    assert result.symbols == ["KEEPUSDT"]
    assert any(
        reject.symbol == "MISSLASTUSDT" and reject.reason == "no_volume_data"
        for reject in result.rejects
    )


def test_invalid_volume_rejects_missing_24h_stats() -> None:
    """When volume data fails to parse (empty string), reject with missing_24h_stats.

    AD-101: missing_24h_stats is used for parse errors (like empty string for volume).
    """
    client = StubClient(
        exchange_info={
            "symbols": [
                {"symbol": "KEEPUSDT", "quoteAsset": "USDT", "status": "1"},
                {"symbol": "MISSVOLUSDT", "quoteAsset": "USDT", "status": "1"},
            ]
        },
        default_symbols=["KEEPUSDT", "MISSVOLUSDT"],
        tickers=[
            {"symbol": "KEEPUSDT", "quoteVolume": "1000", "count": 10},
            {
                "symbol": "MISSVOLUSDT",
                "quoteVolume": None,
                "volume": "",  # Empty string causes parse_error
            },
        ],
    )
    cfg = UniverseConfig(min_quote_volume_24h=0, min_trades_24h=0)

    result = build_universe(client, cfg)

    assert result.symbols == ["KEEPUSDT"]
    assert any(
        reject.symbol == "MISSVOLUSDT" and reject.reason == "missing_24h_stats"
        for reject in result.rejects
    )
