from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

from scanner.config import UniverseConfig
from scanner.models.universe import UniverseReject, UniverseResult, UniverseStats
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

    exchange_info = client.get_exchange_info()
    symbols_payload = exchange_info.get("symbols", [])
    candidates: list[str] = []
    status_rejects: dict[str, str] = {}

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
        if status is not None:
            status_value = str(status)
            if status_value not in cfg.allowed_exchange_status:
                status_rejects[symbol] = status_value

    default_symbols = client.get_default_symbols()
    if not default_symbols:
        raise UniverseBuildError("defaultSymbols empty or unavailable; cannot build universe")
    default_set = set(default_symbols)

    rejects: list[UniverseReject] = []
    tradable: list[str] = []
    for symbol in candidates:
        if symbol in status_rejects:
            rejects.append(
                UniverseReject(symbol=symbol, reason="exchange_status_not_allowed")
            )
        elif symbol not in default_set:
            rejects.append(UniverseReject(symbol=symbol, reason="not_in_default_symbols"))
        else:
            tradable.append(symbol)

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
