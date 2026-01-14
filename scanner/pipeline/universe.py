from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Iterable

from scanner.config import UniverseConfig
from scanner.models.universe import UniverseReject, UniverseResult, UniverseStats
from scanner.obs.logging import log_event


class UniverseBuildError(RuntimeError):
    """Raised when the universe cannot be built safely."""


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _top_rejects(rejects: Iterable[UniverseReject], limit: int = 5) -> list[dict[str, int | str]]:
    counter = Counter(reject.reason for reject in rejects)
    return [
        {"reason": reason, "count": count}
        for reason, count in counter.most_common(limit)
    ]


def build_universe(client: object, cfg: UniverseConfig) -> UniverseResult:
    logger = logging.getLogger(__name__)

    exchange_info = client.get_exchange_info()
    symbols_payload = exchange_info.get("symbols", [])
    candidates: list[str] = []
    unexpected_status: list[str] = []

    for entry in symbols_payload:
        if not isinstance(entry, dict):
            continue
        if entry.get("quoteAsset") != cfg.quote_asset:
            continue
        symbol = entry.get("symbol")
        if not isinstance(symbol, str):
            continue
        candidates.append(symbol)
        status = entry.get("status")
        if status and status != "TRADING":
            unexpected_status.append(f"{symbol}:{status}")

    if unexpected_status:
        log_event(
            logger,
            logging.WARNING,
            "universe_unexpected_status",
            "Unexpected symbol status in exchangeInfo",
            count=len(unexpected_status),
            sample=unexpected_status[:5],
        )

    default_symbols = client.get_default_symbols()
    if not default_symbols:
        raise UniverseBuildError("defaultSymbols empty or unavailable; cannot build universe")
    default_set = set(default_symbols)

    rejects: list[UniverseReject] = []
    tradable: list[str] = []
    for symbol in candidates:
        if symbol not in default_set:
            rejects.append(UniverseReject(symbol=symbol, reason="not_in_default_symbols"))
        else:
            tradable.append(symbol)

    tickers = client.get_ticker_24hr()
    ticker_map = {
        entry.get("symbol"): entry
        for entry in tickers
        if isinstance(entry, dict) and entry.get("symbol")
    }

    blacklist_patterns = [re.compile(pattern) for pattern in cfg.blacklist_regex]
    whitelist = set(cfg.whitelist)
    kept: list[str] = []

    for symbol in tradable:
        if any(pattern.search(symbol) for pattern in blacklist_patterns):
            rejects.append(UniverseReject(symbol=symbol, reason="blacklisted"))
            continue

        ticker = ticker_map.get(symbol)
        if not ticker:
            rejects.append(UniverseReject(symbol=symbol, reason="missing_24h_ticker"))
            continue

        if symbol in whitelist:
            log_event(
                logger,
                logging.INFO,
                "universe_whitelist_bypass",
                "Whitelist symbol bypassed 24h filters",
                symbol=symbol,
            )
            kept.append(symbol)
            continue

        quote_volume = _parse_float(ticker.get("quoteVolume"))
        if quote_volume is None and cfg.use_quote_volume_estimate:
            volume = _parse_float(ticker.get("volume"))
            last_price = _parse_float(ticker.get("lastPrice"))
            if volume is None:
                rejects.append(UniverseReject(symbol=symbol, reason="missing_24h_volume"))
                continue
            if last_price is None or last_price <= 0:
                rejects.append(
                    UniverseReject(symbol=symbol, reason="missing_last_price_for_estimate")
                )
                continue
            quote_volume = volume * last_price
            log_event(
                logger,
                logging.INFO,
                "universe_volume_estimated",
                "quoteVolume missing; estimated notional volume",
                symbol=symbol,
                volume=volume,
                lastPrice=last_price,
                quoteVolume_est=quote_volume,
            )

        if quote_volume is None:
            rejects.append(UniverseReject(symbol=symbol, reason="missing_24h_volume"))
            continue

        trade_count = _parse_int(ticker.get("count"))
        if trade_count is None and cfg.require_trade_count:
            rejects.append(UniverseReject(symbol=symbol, reason="missing_trade_count"))
            continue

        if quote_volume < cfg.min_quote_volume_24h:
            rejects.append(UniverseReject(symbol=symbol, reason="min_quote_volume_24h"))
            continue

        if trade_count is not None and trade_count < cfg.min_trades_24h:
            rejects.append(UniverseReject(symbol=symbol, reason="min_trades_24h"))
            continue

        kept.append(symbol)

    top_rejects = _top_rejects(rejects)
    if not kept:
        stats = UniverseStats(total=len(candidates), kept=0, rejected=len(rejects))
        log_event(
            logger,
            logging.INFO,
            "universe_reject_summary",
            "Universe reject summary",
            total=stats.total,
            kept=stats.kept,
            rejected=stats.rejected,
            top_reject_reasons=top_rejects,
        )
        log_event(
            logger,
            logging.ERROR,
            "universe_empty",
            "Universe filtered to 0 symbols",
            total=stats.total,
            kept=stats.kept,
            rejected=stats.rejected,
            top_reject_reasons=top_rejects,
        )
        raise UniverseBuildError("Universe filtered to 0 symbols; relax thresholds")

    stats = UniverseStats(total=len(candidates), kept=len(kept), rejected=len(rejects))

    log_event(
        logger,
        logging.INFO,
        "universe_reject_summary",
        "Universe reject summary",
        total=stats.total,
        kept=stats.kept,
        rejected=stats.rejected,
        top_reject_reasons=top_rejects,
    )
    log_event(
        logger,
        logging.INFO,
        "universe_built",
        "Universe built",
        total=stats.total,
        kept=stats.kept,
        rejected=stats.rejected,
        top_reject_reasons=top_rejects,
    )

    return UniverseResult(symbols=kept, rejects=rejects, stats=stats)
