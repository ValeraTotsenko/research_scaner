from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from scanner.config import SamplingConfig
from scanner.io.raw_writer import RawJsonlWriter, create_raw_bookticker_writer
from scanner.mexc.errors import FatalHttpError, RateLimitedError, TransientHttpError
from scanner.models.spread import SpreadSampleResult, compute_spread_bps
from scanner.obs.logging import log_event


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quote_payload(entry: dict) -> tuple[str | None, object | None, object | None]:
    symbol = entry.get("symbol")
    bid_value = entry.get("bidPrice", entry.get("bid"))
    ask_value = entry.get("askPrice", entry.get("ask"))
    return symbol if isinstance(symbol, str) else None, bid_value, ask_value


def run_spread_sampling(
    client: object,
    symbols: list[str],
    cfg: SamplingConfig,
    out_dir: Path,
) -> SpreadSampleResult:
    logger = logging.getLogger(__name__)
    spread_cfg = cfg.spread
    if spread_cfg.interval_s <= 0:
        raise ValueError("interval_s must be positive")
    if spread_cfg.duration_s <= 0:
        raise ValueError("duration_s must be positive")

    universe_set = set(symbols)
    target_ticks = max(1, math.ceil(spread_cfg.duration_s / spread_cfg.interval_s))
    tick_success = 0
    tick_fail = 0
    invalid_count = 0
    missing_count = 0

    raw_writer: RawJsonlWriter | None = None
    try:
        if cfg.raw.enabled:
            raw_writer = create_raw_bookticker_writer(out_dir, gzip_enabled=cfg.raw.gzip)
            raw_writer.__enter__()

        start = time.monotonic()
        for tick_idx in range(target_ticks):
            tick_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            symbols_seen: set[str] = set()
            latency_ms = None
            payload: list[dict] | None = None

            try:
                req_start = time.monotonic()
                payload = client.get_book_ticker()
                latency_ms = round((time.monotonic() - req_start) * 1000, 2)
                tick_success += 1
            except FatalHttpError as exc:
                if spread_cfg.allow_per_symbol:
                    if len(symbols) > spread_cfg.per_symbol_limit:
                        tick_fail += 1
                        missing_count += len(symbols)
                        log_event(
                            logger,
                            logging.WARNING,
                            "spread_tick_skip",
                            "Per-symbol fallback skipped due to symbol limit",
                            tick_idx=tick_idx,
                            symbol_count=len(symbols),
                            per_symbol_limit=spread_cfg.per_symbol_limit,
                        )
                    else:
                        per_symbol_payload: list[dict] = []
                        per_symbol_failures = 0
                        req_start = time.monotonic()
                        for symbol in symbols:
                            try:
                                per_symbol_payload.append(client.get_book_ticker_symbol(symbol))
                            except (RateLimitedError, TransientHttpError, FatalHttpError):
                                per_symbol_failures += 1
                        latency_ms = round((time.monotonic() - req_start) * 1000, 2)
                        if per_symbol_payload:
                            payload = per_symbol_payload
                            tick_success += 1
                        else:
                            tick_fail += 1
                        if per_symbol_failures:
                            log_event(
                                logger,
                                logging.WARNING,
                                "spread_tick_partial",
                                "Per-symbol fallback had failures",
                                tick_idx=tick_idx,
                                failures=per_symbol_failures,
                            )
                else:
                    tick_fail += 1
                    log_event(
                        logger,
                        logging.WARNING,
                        "spread_tick_fail",
                        "Bulk bookTicker failed; per-symbol fallback disabled",
                        tick_idx=tick_idx,
                        error=str(exc),
                    )
            except (RateLimitedError, TransientHttpError) as exc:
                tick_fail += 1
                log_event(
                    logger,
                    logging.WARNING,
                    "spread_tick_fail",
                    "Bulk bookTicker failed",
                    tick_idx=tick_idx,
                    error=str(exc),
                )

            if payload:
                for entry in payload:
                    if not isinstance(entry, dict):
                        continue
                    symbol, bid_value, ask_value = _quote_payload(entry)
                    if symbol is None or symbol not in universe_set:
                        continue
                    bid = _parse_float(bid_value)
                    ask = _parse_float(ask_value)
                    if bid is None or ask is None or bid <= 0 or ask <= 0:
                        invalid_count += 1
                        continue
                    symbols_seen.add(symbol)
                    _ = compute_spread_bps(bid, ask)
                    if raw_writer:
                        raw_writer.write(
                            {
                                "ts": tick_ts,
                                "symbol": symbol,
                                "bid": str(bid_value),
                                "ask": str(ask_value),
                            }
                        )

            missing_for_tick = len(universe_set - symbols_seen)
            if payload is not None:
                missing_count += missing_for_tick

            log_event(
                logger,
                logging.INFO,
                "spread_tick",
                "Spread tick collected",
                tick_idx=tick_idx,
                symbols_seen=len(symbols_seen),
                latency_ms=latency_ms,
            )

            next_deadline = start + (tick_idx + 1) * spread_cfg.interval_s
            now = time.monotonic()
            sleep_s = next_deadline - now
            if sleep_s > 0:
                time.sleep(sleep_s)
    finally:
        if raw_writer:
            raw_writer.close()

    ticks_total = tick_success + tick_fail
    uptime = tick_success / target_ticks if target_ticks else 0.0
    low_quality = uptime < spread_cfg.min_uptime

    log_event(
        logger,
        logging.INFO,
        "spread_sampling_done",
        "Spread sampling finished",
        uptime=round(uptime, 4),
        invalid_count=invalid_count,
        missing_count=missing_count,
        ticks_total=ticks_total,
        ticks_success=tick_success,
        ticks_fail=tick_fail,
    )

    return SpreadSampleResult(
        target_ticks=target_ticks,
        ticks_success=tick_success,
        ticks_fail=tick_fail,
        invalid_quotes=invalid_count,
        missing_quotes=missing_count,
        uptime=uptime,
        low_quality=low_quality,
        raw_path=raw_writer.path if raw_writer else None,
    )
