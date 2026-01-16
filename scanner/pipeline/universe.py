from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

from scanner.config import UniverseConfig
from scanner.models.universe import UniverseReject, UniverseResult, UniverseStats
from scanner.obs.metrics import update_metrics
from scanner.obs.logging import log_event
from scanner.pipeline.ticker_24h import build_ticker24h_stats


class UniverseBuildError(RuntimeError):
    """Raised when the universe cannot be built safely."""


def _top_rejects(rejects: Iterable[UniverseReject], limit: int = 5) -> list[dict[str, int | str]]:
    counter = Counter(reject.reason for reject in rejects)
    return [
        {"reason": reason, "count": count}
        for reason, count in counter.most_common(limit)
    ]


def build_universe(
    client: object,
    cfg: UniverseConfig,
    *,
    logger: logging.Logger | None = None,
    metrics_path: Path | None = None,
) -> UniverseResult:
    logger = logger or logging.getLogger(__name__)

    try:
        default_symbols = client.get_default_symbols()
    except Exception:
        if metrics_path:
            update_metrics(metrics_path, increments={"defaultSymbols_fetch_fail_total": 1})
        log_event(
            logger,
            logging.ERROR,
            "default_symbols_fetch_failed",
            "Failed to fetch defaultSymbols",
            api_unstable=True,
        )
        raise

    if not default_symbols:
        if metrics_path:
            update_metrics(metrics_path, increments={"defaultSymbols_fetch_fail_total": 1})
        log_event(
            logger,
            logging.ERROR,
            "default_symbols_empty",
            "defaultSymbols empty or unavailable; cannot build universe",
            api_unstable=True,
        )
        raise UniverseBuildError("defaultSymbols empty or unavailable; cannot build universe")

    default_set = set(default_symbols)
    if metrics_path:
        update_metrics(metrics_path, gauges={"defaultSymbols_total": len(default_set)})

    exchange_info = client.get_exchange_info()
    symbols_payload = exchange_info.get("symbols", [])
    exchange_map: dict[str, dict[str, object]] = {}
    exchange_quote_asset: dict[str, str | None] = {}
    exchange_status: dict[str, str | None] = {}

    for entry in symbols_payload:
        if not isinstance(entry, dict):
            continue
        symbol = entry.get("symbol")
        if not isinstance(symbol, str):
            continue
        exchange_map[symbol] = entry
        quote_asset = entry.get("quoteAsset")
        exchange_quote_asset[symbol] = str(quote_asset) if quote_asset is not None else None
        status = entry.get("status")
        exchange_status[symbol] = str(status) if status is not None else None

    candidates = sorted(default_set | set(exchange_map.keys()))
    rejects: list[UniverseReject] = []
    tradable: list[str] = []
    not_in_default_count = 0
    source_flags: dict[str, dict[str, object]] = {}

    for symbol in candidates:
        in_default = symbol in default_set
        in_exchange = symbol in exchange_map
        status_value = exchange_status.get(symbol)
        quote_asset_value = exchange_quote_asset.get(symbol)
        status_allowed = status_value in cfg.allowed_exchange_status if status_value is not None else None
        quote_asset_allowed = (
            quote_asset_value == cfg.quote_asset if quote_asset_value is not None else None
        )

        source_flags[symbol] = {
            "in_defaultSymbols": in_default,
            "in_exchangeInfo": in_exchange,
            "status": status_value,
            "quoteAsset": quote_asset_value,
            "status_allowed": status_allowed,
            "quote_asset_allowed": quote_asset_allowed,
        }

        if not in_default:
            rejects.append(UniverseReject(symbol=symbol, reason="not_in_defaultSymbols"))
            not_in_default_count += 1
            continue
        if not in_exchange:
            rejects.append(UniverseReject(symbol=symbol, reason="metadata_missing"))
            continue
        if quote_asset_value != cfg.quote_asset:
            rejects.append(UniverseReject(symbol=symbol, reason="quote_asset_not_allowed"))
            continue
        if status_value is None or status_value not in cfg.allowed_exchange_status:
            log_event(
                logger,
                logging.WARNING,
                "exchange_status_unexpected",
                "Unexpected exchangeInfo status",
                symbol=symbol,
                status=status_value,
            )
            rejects.append(UniverseReject(symbol=symbol, reason="status_not_allowed"))
            continue
        tradable.append(symbol)

    if metrics_path:
        update_metrics(
            metrics_path,
            increments={"universe_not_in_defaultSymbols_total": not_in_default_count},
        )

    tickers = client.get_ticker_24hr()
    book_tickers = client.get_book_ticker()
    ticker_stats = build_ticker24h_stats(
        tickers,
        book_tickers,
        symbols=tradable,
        use_quote_volume_estimate=cfg.use_quote_volume_estimate,
        require_trade_count=cfg.require_trade_count,
        logger=logger,
        metrics_path=metrics_path,
        log_summary=True,
    )

    blacklist_patterns = [re.compile(pattern) for pattern in cfg.blacklist_regex]
    whitelist = set(cfg.whitelist)
    kept: list[str] = []

    for symbol in tradable:
        if any(pattern.search(symbol) for pattern in blacklist_patterns):
            rejects.append(UniverseReject(symbol=symbol, reason="blacklisted"))
            continue

        ticker = ticker_stats.get(symbol)
        if ticker is None or ticker.missing_24h_stats:
            rejects.append(UniverseReject(symbol=symbol, reason="missing_24h_stats"))
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

        quote_volume = ticker.quote_volume_effective
        if quote_volume is None:
            rejects.append(UniverseReject(symbol=symbol, reason="missing_24h_stats"))
            continue

        trade_count = ticker.trade_count
        if trade_count is None and cfg.require_trade_count:
            rejects.append(UniverseReject(symbol=symbol, reason="missing_trade_count"))
            continue

        if quote_volume < cfg.min_quote_volume_24h:
            rejects.append(UniverseReject(symbol=symbol, reason="low_volume"))
            continue

        if trade_count is not None and trade_count < cfg.min_trades_24h:
            rejects.append(UniverseReject(symbol=symbol, reason="low_trades"))
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

    return UniverseResult(symbols=kept, rejects=rejects, stats=stats, source_flags=source_flags)
