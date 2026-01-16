from scanner.pipeline.ticker_24h import build_ticker24h_stats


def test_quote_volume_estimated_with_mid() -> None:
    ticker_payload = [{"symbol": "BTCUSDT", "quoteVolume": None, "volume": "10"}]
    book_payload = [{"symbol": "BTCUSDT", "bidPrice": "1.5", "askPrice": "2.5"}]

    stats = build_ticker24h_stats(
        ticker_payload,
        book_payload,
        symbols=["BTCUSDT"],
        use_quote_volume_estimate=True,
        require_trade_count=False,
    )["BTCUSDT"]

    assert stats.missing_24h_stats is False
    assert stats.quote_volume_effective == 20.0
    assert stats.used_estimate is True


def test_missing_when_no_volume_and_no_mid() -> None:
    ticker_payload = [{"symbol": "ETHUSDT", "quoteVolume": None, "volume": None}]
    book_payload: list[dict[str, str]] = []

    stats = build_ticker24h_stats(
        ticker_payload,
        book_payload,
        symbols=["ETHUSDT"],
        use_quote_volume_estimate=True,
        require_trade_count=False,
    )["ETHUSDT"]

    assert stats.missing_24h_stats is True
    assert stats.quote_volume_effective is None


def test_count_null_not_missing_when_not_required() -> None:
    ticker_payload = [{"symbol": "SOLUSDT", "quoteVolume": "100", "count": None}]
    book_payload: list[dict[str, str]] = []

    stats = build_ticker24h_stats(
        ticker_payload,
        book_payload,
        symbols=["SOLUSDT"],
        use_quote_volume_estimate=True,
        require_trade_count=False,
    )["SOLUSDT"]

    assert stats.missing_24h_stats is False


def test_parse_error_marks_missing() -> None:
    ticker_payload = [{"symbol": "XRPUSDT", "quoteVolume": "oops"}]
    book_payload: list[dict[str, str]] = []

    stats = build_ticker24h_stats(
        ticker_payload,
        book_payload,
        symbols=["XRPUSDT"],
        use_quote_volume_estimate=True,
        require_trade_count=False,
    )["XRPUSDT"]

    assert stats.missing_24h_stats is True
    assert stats.missing_24h_reason == "parse_error"


def test_bad_mid_price_keeps_missing() -> None:
    ticker_payload = [{"symbol": "ADAUSDT", "quoteVolume": None, "volume": "5"}]
    book_payload = [{"symbol": "ADAUSDT", "bidPrice": "0", "askPrice": "0"}]

    stats = build_ticker24h_stats(
        ticker_payload,
        book_payload,
        symbols=["ADAUSDT"],
        use_quote_volume_estimate=True,
        require_trade_count=False,
    )["ADAUSDT"]

    assert stats.missing_24h_stats is True
    assert stats.mid_price is None
