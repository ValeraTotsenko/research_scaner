from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scanner.obs.logging import log_event
from scanner.obs.metrics import update_metrics


@dataclass(frozen=True)
class Ticker24hStats:
    symbol: str
    quote_volume_raw: float | None
    volume_raw: float | None
    mid_price: float | None
    quote_volume_est: float | None
    quote_volume_effective: float | None
    trade_count: int | None
    missing_24h_stats: bool
    missing_24h_reason: str | None
    used_estimate: bool


@dataclass(frozen=True)
class _ParsedTickerRow:
    quote_volume_raw: float | None
    volume_raw: float | None
    trade_count: int | None
    parse_error: bool


def _parse_float(value: object) -> tuple[float | None, bool]:
    if value is None:
        return None, True
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None, False
    if not math.isfinite(parsed):
        return None, False
    return parsed, True


def _parse_int(value: object) -> tuple[int | None, bool]:
    if value is None:
        return None, True
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None, False
    if not math.isfinite(parsed):
        return None, False
    return parsed, True


def _mid_price(entry: dict[str, object]) -> float | None:
    bid, bid_ok = _parse_float(entry.get("bidPrice"))
    ask, ask_ok = _parse_float(entry.get("askPrice"))
    if not (bid_ok and ask_ok):
        return None
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    if not math.isfinite(mid) or mid <= 0:
        return None
    return mid


def build_ticker24h_stats(
    ticker_payload: Iterable[object],
    book_payload: Iterable[object],
    *,
    symbols: Iterable[str] | None = None,
    use_quote_volume_estimate: bool,
    require_trade_count: bool,
    logger: logging.Logger | None = None,
    metrics_path: Path | None = None,
    log_summary: bool = True,
) -> dict[str, Ticker24hStats]:
    ticker_rows = list(ticker_payload)
    total_rows = len(ticker_rows)
    parse_errors = 0

    ticker_map: dict[str, _ParsedTickerRow] = {}
    for entry in ticker_rows:
        if not isinstance(entry, dict):
            parse_errors += 1
            continue
        symbol = entry.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            parse_errors += 1
            continue
        quote_volume, quote_ok = _parse_float(entry.get("quoteVolume"))
        volume, volume_ok = _parse_float(entry.get("volume"))
        trade_count, count_ok = _parse_int(entry.get("count"))
        parse_error = (not quote_ok) or (not volume_ok)
        if require_trade_count and not count_ok:
            parse_error = True
        if parse_error:
            parse_errors += 1
        ticker_map[symbol] = _ParsedTickerRow(
            quote_volume_raw=quote_volume,
            volume_raw=volume,
            trade_count=trade_count if count_ok else None,
            parse_error=parse_error,
        )

    mid_map: dict[str, float] = {}
    for entry in book_payload:
        if not isinstance(entry, dict):
            continue
        symbol = entry.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            continue
        mid = _mid_price(entry)
        if mid is not None:
            mid_map[symbol] = mid

    if logger and log_summary:
        log_event(
            logger,
            logging.INFO,
            "ticker24h_parsed",
            "Parsed ticker/24hr payload",
            total_rows=total_rows,
            parse_errors=parse_errors,
        )

    stats: dict[str, Ticker24hStats] = {}
    used_est_total = 0

    symbol_list = list(symbols) if symbols is not None else list(ticker_map.keys())
    for symbol in symbol_list:
        row = ticker_map.get(symbol)
        if row is None:
            stats[symbol] = Ticker24hStats(
                symbol=symbol,
                quote_volume_raw=None,
                volume_raw=None,
                mid_price=mid_map.get(symbol),
                quote_volume_est=None,
                quote_volume_effective=None,
                trade_count=None,
                missing_24h_stats=True,
                missing_24h_reason="no_row",
                used_estimate=False,
            )
            continue

        if row.parse_error:
            stats[symbol] = Ticker24hStats(
                symbol=symbol,
                quote_volume_raw=row.quote_volume_raw,
                volume_raw=row.volume_raw,
                mid_price=mid_map.get(symbol),
                quote_volume_est=None,
                quote_volume_effective=None,
                trade_count=row.trade_count,
                missing_24h_stats=True,
                missing_24h_reason="parse_error",
                used_estimate=False,
            )
            continue

        mid_price = mid_map.get(symbol)
        quote_volume_est = None
        quote_volume_effective = row.quote_volume_raw
        used_estimate = False

        if quote_volume_effective is None and use_quote_volume_estimate:
            if row.volume_raw is not None and mid_price is not None:
                quote_volume_est = row.volume_raw * mid_price
                quote_volume_effective = quote_volume_est
                used_estimate = True

        missing = False
        missing_reason = None
        if row.quote_volume_raw is None and row.volume_raw is None:
            missing = True
            missing_reason = "no_any_fields"
        elif row.quote_volume_raw is None and quote_volume_effective is None:
            missing = True
            missing_reason = "no_volume_and_no_mid"
        if require_trade_count and row.trade_count is None:
            missing = True
            missing_reason = missing_reason or "missing_trade_count"

        if logger and quote_volume_effective is not None:
            log_event(
                logger,
                logging.INFO,
                "ticker24h_effective_volume_computed",
                "Computed effective 24h quote volume",
                symbol=symbol,
                used_est=used_estimate,
                quoteVolume_effective=quote_volume_effective,
                quoteVolume_raw=row.quote_volume_raw,
                quoteVolume_est=quote_volume_est,
            )

        if used_estimate:
            used_est_total += 1

        stats[symbol] = Ticker24hStats(
            symbol=symbol,
            quote_volume_raw=row.quote_volume_raw,
            volume_raw=row.volume_raw,
            mid_price=mid_price,
            quote_volume_est=quote_volume_est,
            quote_volume_effective=quote_volume_effective,
            trade_count=row.trade_count,
            missing_24h_stats=missing,
            missing_24h_reason=missing_reason,
            used_estimate=used_estimate,
        )

    if metrics_path:
        missing_count = sum(1 for item in stats.values() if item.missing_24h_stats)
        update_metrics(
            metrics_path,
            increments={
                "ticker24h_rows_total": total_rows,
                "ticker24h_parse_fail_total": parse_errors,
                "quoteVolume_est_used_total": used_est_total,
            },
            gauges={"missing_24h_stats_symbols": missing_count},
        )

    return stats
