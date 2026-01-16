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


def test_no_volume_data_but_not_missing() -> None:
    """AD-101: API returning null for quoteVolume/volume is valid, not 'missing'.

    missing_24h_stats is only True for 'no_row' or 'parse_error'.
    If both quoteVolume and volume are null, we have no volume data but
    the symbol exists with valid null values.
    """
    ticker_payload = [{"symbol": "ETHUSDT", "quoteVolume": None, "volume": None}]
    book_payload: list[dict[str, str]] = []

    stats = build_ticker24h_stats(
        ticker_payload,
        book_payload,
        symbols=["ETHUSDT"],
        use_quote_volume_estimate=True,
        require_trade_count=False,
    )["ETHUSDT"]

    # AD-101: API returning null is valid, not "missing"
    assert stats.missing_24h_stats is False
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


def test_bad_mid_price_no_estimate_but_not_missing() -> None:
    """AD-101: Even if we can't compute estimate, API data is valid (not 'missing').

    missing_24h_stats is only True for 'no_row' or 'parse_error'.
    If quoteVolume is null and mid_price is invalid (can't estimate),
    the symbol still has valid null volume data.
    """
    ticker_payload = [{"symbol": "ADAUSDT", "quoteVolume": None, "volume": "5"}]
    book_payload = [{"symbol": "ADAUSDT", "bidPrice": "0", "askPrice": "0"}]

    stats = build_ticker24h_stats(
        ticker_payload,
        book_payload,
        symbols=["ADAUSDT"],
        use_quote_volume_estimate=True,
        require_trade_count=False,
    )["ADAUSDT"]

    # AD-101: API data exists (not "missing"), just can't estimate
    assert stats.missing_24h_stats is False
    assert stats.mid_price is None
    assert stats.quote_volume_effective is None  # Can't estimate without valid mid
