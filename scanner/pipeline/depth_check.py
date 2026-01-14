from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from scanner.analytics.depth_metrics import aggregate_depth_metrics, compute_snapshot_metrics
from scanner.analytics.scoring import ScoreResult
from scanner.config import AppConfig
from scanner.io.depth_export import export_depth_metrics, export_summary_enriched
from scanner.mexc.errors import FatalHttpError, RateLimitedError, TransientHttpError
from scanner.models.depth import DepthCheckResult, DepthSymbolMetrics
from scanner.obs.logging import log_event


@dataclass
class _DepthSymbolState:
    symbol: str
    snapshots: list = field(default_factory=list)
    sample_count: int = 0
    valid_samples: int = 0
    empty_book_count: int = 0
    invalid_book_count: int = 0
    symbol_unavailable_count: int = 0


def _select_candidates(candidates: Sequence[object], limit: int) -> list[str]:
    if not candidates:
        return []
    if all(isinstance(item, str) for item in candidates):
        return list(candidates)

    score_items: list[ScoreResult] = [item for item in candidates if isinstance(item, ScoreResult)]
    if not score_items:
        raise ValueError("Candidates must be strings or ScoreResult entries")

    pass_spread = [item for item in score_items if item.pass_spread]
    if pass_spread:
        return [item.symbol for item in pass_spread]

    sorted_items = sorted(score_items, key=lambda item: (-item.score, item.symbol))
    return [item.symbol for item in sorted_items[:limit]]


def _classify_snapshot_error(exc: ValueError) -> str:
    message = str(exc)
    if "Empty book" in message:
        return "empty_book"
    if "Depth level" in message:
        return "invalid_book_levels"
    return "invalid_book_levels"


def run_depth_check(
    client: object,
    candidates: Sequence[object],
    cfg: AppConfig,
    out_dir: Path,
    *,
    deadline_ts: float | None = None,
) -> DepthCheckResult:
    logger = logging.getLogger(__name__)
    depth_sampling = cfg.sampling.depth
    depth_cfg = cfg.depth
    thresholds = cfg.thresholds.depth

    if depth_sampling.interval_s <= 0:
        raise ValueError("interval_s must be positive")
    if depth_sampling.duration_s <= 0:
        raise ValueError("duration_s must be positive")
    if depth_sampling.limit <= 0 or depth_sampling.limit > 5000:
        raise ValueError("depth sampling limit must be between 1 and 5000")
    if depth_cfg.top_n_levels <= 0:
        raise ValueError("top_n_levels must be positive")

    symbols = _select_candidates(candidates, limit=50)
    if not symbols:
        raise ValueError("No depth candidates provided")

    symbol_states = {symbol: _DepthSymbolState(symbol=symbol) for symbol in symbols}
    target_ticks = max(1, math.ceil(depth_sampling.duration_s / depth_sampling.interval_s))
    ticks_success = 0
    ticks_fail = 0
    depth_requests_total = 0
    depth_fail_total = 0

    start = time.monotonic()
    timed_out = False
    timeout_s = max(0.0, deadline_ts - start) if deadline_ts is not None else None
    backoff_s = 0.5

    for tick_idx in range(target_ticks):
        if deadline_ts is not None and time.monotonic() > deadline_ts:
            timed_out = True
            log_event(
                logger,
                logging.WARNING,
                "stage_timeout_warning",
                "Stage deadline reached during depth sampling",
                stage="depth",
                elapsed_s=round(time.monotonic() - start, 2),
                timeout_s=timeout_s,
                tick_idx=tick_idx,
            )
            break
        tick_successful = False
        for symbol in symbols:
            if deadline_ts is not None and time.monotonic() > deadline_ts:
                timed_out = True
                log_event(
                    logger,
                    logging.WARNING,
                    "stage_timeout_warning",
                    "Stage deadline reached during depth sampling",
                    stage="depth",
                    elapsed_s=round(time.monotonic() - start, 2),
                    timeout_s=timeout_s,
                    tick_idx=tick_idx,
                )
                break
            depth_requests_total += 1
            state = symbol_states[symbol]
            latency_ms = None
            req_start = time.monotonic()
            try:
                payload = client.get_depth(symbol, depth_sampling.limit)
                latency_ms = round((time.monotonic() - req_start) * 1000, 2)
                bids = payload.get("bids", [])
                asks = payload.get("asks", [])
                metrics = compute_snapshot_metrics(
                    bids,
                    asks,
                    top_n=depth_cfg.top_n_levels,
                    band_bps=depth_cfg.band_bps,
                    stress_notional=depth_cfg.stress_notional_usdt,
                )
                state.snapshots.append(metrics)
                state.sample_count += 1
                state.valid_samples += 1
                tick_successful = True
                backoff_s = 0.5
                log_event(
                    logger,
                    logging.INFO,
                    "depth_tick",
                    "Depth snapshot collected",
                    symbol=symbol,
                    levels={"bids": len(bids), "asks": len(asks)},
                    latency_ms=latency_ms,
                    tick_idx=tick_idx,
                )
            except ValueError as exc:
                depth_fail_total += 1
                state.sample_count += 1
                reason = _classify_snapshot_error(exc)
                if reason == "empty_book":
                    state.empty_book_count += 1
                else:
                    state.invalid_book_count += 1
                log_event(
                    logger,
                    logging.WARNING,
                    "depth_tick_invalid",
                    "Depth snapshot invalid",
                    symbol=symbol,
                    reason=reason,
                    tick_idx=tick_idx,
                )
            except FatalHttpError as exc:
                depth_fail_total += 1
                state.symbol_unavailable_count += 1
                log_event(
                    logger,
                    logging.WARNING,
                    "depth_tick_unavailable",
                    "Depth snapshot unavailable",
                    symbol=symbol,
                    error=str(exc),
                    tick_idx=tick_idx,
                )
            except (RateLimitedError, TransientHttpError) as exc:
                depth_fail_total += 1
                log_event(
                    logger,
                    logging.WARNING,
                    "depth_tick_fail",
                    "Depth snapshot failed",
                    symbol=symbol,
                    error=str(exc),
                    tick_idx=tick_idx,
                )
                time.sleep(backoff_s)
                backoff_s = min(backoff_s * 2, 8)

        if timed_out:
            break

        if tick_successful:
            ticks_success += 1
        else:
            ticks_fail += 1

        next_deadline = start + (tick_idx + 1) * depth_sampling.interval_s
        sleep_s = next_deadline - time.monotonic()
        if sleep_s > 0:
            time.sleep(sleep_s)

    elapsed_s = time.monotonic() - start
    results: list[DepthSymbolMetrics] = []
    for symbol in symbols:
        state = symbol_states[symbol]
        aggregates = aggregate_depth_metrics(state.snapshots, band_bps=depth_cfg.band_bps)
        uptime = state.valid_samples / target_ticks if target_ticks else 0.0
        fail_reasons: list[str] = []
        if state.empty_book_count:
            fail_reasons.append("empty_book")
        if state.invalid_book_count:
            fail_reasons.append("invalid_book_levels")
        if state.symbol_unavailable_count:
            fail_reasons.append("symbol_unavailable")
        if state.valid_samples == 0:
            fail_reasons.append("no_valid_samples")

        best_bid_median = aggregates["best_bid_notional_median"]
        best_ask_median = aggregates["best_ask_notional_median"]
        unwind_p90 = aggregates["unwind_slippage_p90_bps"]

        if best_bid_median is None or best_ask_median is None:
            fail_reasons.append("missing_best_level_notional")
        else:
            if best_bid_median < thresholds.best_level_min_notional:
                fail_reasons.append("best_bid_notional_low")
            if best_ask_median < thresholds.best_level_min_notional:
                fail_reasons.append("best_ask_notional_low")

        if unwind_p90 is None:
            fail_reasons.append("missing_unwind_slippage")
        elif unwind_p90 > thresholds.unwind_slippage_max_bps:
            fail_reasons.append("unwind_slippage_high")

        pass_depth = len(fail_reasons) == 0

        results.append(
            DepthSymbolMetrics(
                symbol=symbol,
                sample_count=state.sample_count,
                valid_samples=state.valid_samples,
                empty_book_count=state.empty_book_count,
                invalid_book_count=state.invalid_book_count,
                symbol_unavailable_count=state.symbol_unavailable_count,
                best_bid_notional_median=best_bid_median,
                best_ask_notional_median=best_ask_median,
                topn_bid_notional_median=aggregates["topn_bid_notional_median"],
                topn_ask_notional_median=aggregates["topn_ask_notional_median"],
                band_bid_notional_median=aggregates["band_bid_notional_median"],
                unwind_slippage_p90_bps=unwind_p90,
                uptime=uptime,
                pass_depth=pass_depth,
                fail_reasons=tuple(fail_reasons),
            )
        )

    pass_depth_count = sum(1 for result in results if result.pass_depth)
    log_event(
        logger,
        logging.INFO,
        "depth_done",
        "Depth check completed",
        pass_depth_count=pass_depth_count,
    )

    export_depth_metrics(out_dir, results, band_bps=depth_cfg.band_bps)
    if candidates and all(isinstance(item, ScoreResult) for item in candidates):
        export_summary_enriched(out_dir, candidates, results, band_bps=depth_cfg.band_bps)

    return DepthCheckResult(
        target_ticks=target_ticks,
        ticks_success=ticks_success,
        ticks_fail=ticks_fail,
        symbols=tuple(results),
        depth_requests_total=depth_requests_total,
        depth_fail_total=depth_fail_total,
        depth_symbols_pass_total=pass_depth_count,
        timed_out=timed_out,
        elapsed_s=elapsed_s,
    )
