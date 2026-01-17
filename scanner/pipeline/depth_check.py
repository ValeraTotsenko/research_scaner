"""
Depth check stage implementation for order book liquidity analysis.

This module implements the depth stage of the pipeline, which:
1. Selects top candidates from spread scoring results
2. Samples order book depth at regular intervals
3. Computes aggregated liquidity metrics per symbol
4. Evaluates pass/fail criteria for depth thresholds
5. Exports depth_metrics.csv and summary_enriched.csv

Candidate Selection:
    Candidates are sorted by score (descending) and filtered to
    symbols that passed spread criteria. Limited by candidates_limit.

Depth Criteria (PASS_DEPTH requires ALL):
    - best_bid_notional_median >= best_level_min_notional
    - best_ask_notional_median >= best_level_min_notional
    - unwind_slippage_p90_bps <= unwind_slippage_max_bps

Depth Uptime Calculation:
    Depth uptime is calculated as valid_samples / target_ticks, where target_ticks
    accounts for API rate limiting. With max_rps limit, each tick takes:
        tick_duration_s = num_symbols / max_rps

    If tick_duration_s > interval_s, the system operates in "effective snapshot mode"
    and uses: target_ticks = duration_s / tick_duration_s (instead of naive duration/interval).

    Example: 80 symbols at 2 RPS with duration=1200s, interval=30s:
        - tick_duration = 80/2 = 40s (> 30s interval)
        - effective_target = 1200/40 = 30 ticks (not naive 1200/30 = 40)

Note:
    Depth uptime is informational only - NOT a pass/fail criterion.
    This differs from spread uptime which is a pass/fail criterion.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from scanner.analytics.depth_metrics import aggregate_depth_metrics, compute_snapshot_metrics
from scanner.analytics.scoring import ScoreResult
from scanner.config import AppConfig, DepthConfig, DepthThresholdsConfig
from scanner.io.depth_export import export_depth_metrics, export_summary_enriched
from scanner.mexc.errors import FatalHttpError, RateLimitedError, TransientHttpError, WafLimitedError
from scanner.models.depth import DepthCheckResult, DepthSymbolMetrics
from scanner.obs.logging import log_event


@dataclass
class _DepthSymbolState:
    """Internal mutable state for tracking depth samples per symbol."""
    symbol: str
    snapshots: list = field(default_factory=list)
    sample_count: int = 0
    valid_samples: int = 0
    empty_book_count: int = 0
    invalid_book_count: int = 0
    symbol_unavailable_count: int = 0


@dataclass(frozen=True)
class _DepthCriteriaResult:
    best_bid_notional_pass: bool
    best_ask_notional_pass: bool
    unwind_slippage_pass: bool
    band_10bps_notional_pass: bool | None
    topn_notional_pass: bool | None
    fail_reasons: tuple[str, ...]


def _select_candidates(candidates: Sequence[object], limit: int) -> tuple[list[str], int]:
    if not candidates:
        return [], 0
    if all(isinstance(item, str) for item in candidates):
        selected = list(candidates)
        if limit > 0:
            selected = selected[:limit]
        return selected, 0

    score_items: list[ScoreResult] = [item for item in candidates if isinstance(item, ScoreResult)]
    if not score_items:
        raise ValueError("Candidates must be strings or ScoreResult entries")

    pass_spread = [item for item in score_items if item.pass_spread]
    pass_spread_total = len(pass_spread)
    if pass_spread:
        sorted_pass_spread = sorted(pass_spread, key=lambda item: (-item.score, item.symbol))
        if limit > 0:
            sorted_pass_spread = sorted_pass_spread[:limit]
        return [item.symbol for item in sorted_pass_spread], pass_spread_total

    sorted_items = sorted(score_items, key=lambda item: (-item.score, item.symbol))
    if limit > 0:
        sorted_items = sorted_items[:limit]
    return [item.symbol for item in sorted_items], pass_spread_total


def _classify_snapshot_error(exc: ValueError) -> str:
    message = str(exc)
    if "Empty book" in message:
        return "empty_book"
    if "Depth level" in message:
        return "invalid_book_levels"
    return "invalid_book_levels"


def _evaluate_depth_criteria(
    aggregates: dict[str, object],
    *,
    thresholds: DepthThresholdsConfig,
    depth_cfg: DepthConfig,
) -> _DepthCriteriaResult:
    fail_reasons: list[str] = []

    best_bid_median = aggregates["best_bid_notional_median"]
    best_ask_median = aggregates["best_ask_notional_median"]
    unwind_p90 = aggregates["unwind_slippage_p90_bps"]

    if best_bid_median is None:
        best_bid_pass = False
        fail_reasons.append("missing_best_bid_notional")
    else:
        best_bid_pass = best_bid_median >= thresholds.best_level_min_notional
        if not best_bid_pass:
            fail_reasons.append("best_bid_notional_low")

    if best_ask_median is None:
        best_ask_pass = False
        fail_reasons.append("missing_best_ask_notional")
    else:
        best_ask_pass = best_ask_median >= thresholds.best_level_min_notional
        if not best_ask_pass:
            fail_reasons.append("best_ask_notional_low")

    if unwind_p90 is None:
        unwind_slippage_pass = False
        fail_reasons.append("missing_unwind_slippage")
    else:
        unwind_slippage_pass = unwind_p90 <= thresholds.unwind_slippage_max_bps
        if not unwind_slippage_pass:
            fail_reasons.append("unwind_slippage_high")

    band_10bps_pass: bool | None = None
    if depth_cfg.enable_band_checks and thresholds.band_10bps_min_notional is not None:
        band_medians = aggregates["band_bid_notional_median"] or {}
        band_value = band_medians.get(10)
        if band_value is None:
            band_10bps_pass = False
            fail_reasons.append("missing_band_10bps_notional")
        else:
            band_10bps_pass = band_value >= thresholds.band_10bps_min_notional
            if not band_10bps_pass:
                fail_reasons.append("band_10bps_notional_low")

    topn_pass: bool | None = None
    if depth_cfg.enable_topN_checks and thresholds.topN_min_notional is not None:
        topn_bid = aggregates["topn_bid_notional_median"]
        topn_ask = aggregates["topn_ask_notional_median"]
        if topn_bid is None or topn_ask is None:
            topn_pass = False
            fail_reasons.append("missing_topn_notional")
        else:
            topn_pass = min(topn_bid, topn_ask) >= thresholds.topN_min_notional
            if not topn_pass:
                fail_reasons.append("topn_notional_low")

    return _DepthCriteriaResult(
        best_bid_notional_pass=best_bid_pass,
        best_ask_notional_pass=best_ask_pass,
        unwind_slippage_pass=unwind_slippage_pass,
        band_10bps_notional_pass=band_10bps_pass,
        topn_notional_pass=topn_pass,
        fail_reasons=tuple(fail_reasons),
    )


def run_depth_check(
    client: object,
    candidates: Sequence[object],
    cfg: AppConfig,
    out_dir: Path,
    *,
    deadline_ts: float | None = None,
) -> DepthCheckResult:
    """
    Execute depth check stage: sample order books and evaluate liquidity.

    Main entry point for the depth stage. Samples order book depth for
    top-scoring candidates, computes aggregated metrics, evaluates
    pass/fail criteria, and exports results.

    Args:
        client: MexcClient instance for API calls.
        candidates: ScoreResult list from scoring stage (or symbol strings).
        cfg: Application config with depth thresholds and sampling params.
        out_dir: Output directory for depth_metrics.csv.
        deadline_ts: Optional Unix timestamp deadline for timeout.

    Returns:
        DepthCheckResult with per-symbol metrics and aggregate statistics.

    Raises:
        ValueError: If sampling parameters invalid or no candidates.
    """
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

    symbols, pass_spread_total = _select_candidates(candidates, limit=depth_sampling.candidates_limit)
    log_event(
        logger,
        logging.INFO,
        "depth_candidates_selected",
        "Depth candidates selected",
        candidates_total=len(candidates),
        pass_spread_total=pass_spread_total,
        selected=len(symbols),
        selected_for_depth=len(symbols),
        limit=depth_sampling.candidates_limit,
        strategy="score_desc",
    )
    if not symbols:
        raise ValueError("No depth candidates provided")

    symbol_states = {symbol: _DepthSymbolState(symbol=symbol) for symbol in symbols}

    # Calculate realistic target_ticks accounting for rate limiting.
    # With max_rps limit, each full tick (sampling all symbols) takes:
    #   tick_duration_s = len(symbols) / max_rps
    # If tick_duration_s > interval_s, we're in "effective snapshot mode"
    # and can't achieve the naive duration/interval tick count.
    max_rps = cfg.mexc.max_rps
    naive_target_ticks = max(1, math.ceil(depth_sampling.duration_s / depth_sampling.interval_s))

    if max_rps > 0 and len(symbols) > 0:
        tick_duration_s = len(symbols) / max_rps
        if tick_duration_s > depth_sampling.interval_s:
            # Effective snapshot mode: can only achieve fewer ticks
            effective_target_ticks = max(1, int(depth_sampling.duration_s / tick_duration_s))
            log_event(
                logger,
                logging.INFO,
                "depth_snapshot_mode",
                "Depth operating in effective snapshot mode due to rate limiting",
                symbols_count=len(symbols),
                max_rps=max_rps,
                tick_duration_s=round(tick_duration_s, 1),
                interval_s=depth_sampling.interval_s,
                naive_target_ticks=naive_target_ticks,
                effective_target_ticks=effective_target_ticks,
            )
            target_ticks = effective_target_ticks
        else:
            target_ticks = naive_target_ticks
    else:
        target_ticks = naive_target_ticks

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
            except (RateLimitedError, TransientHttpError, WafLimitedError) as exc:
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

        criteria = _evaluate_depth_criteria(
            aggregates,
            thresholds=thresholds,
            depth_cfg=depth_cfg,
        )
        fail_reasons.extend(criteria.fail_reasons)

        best_bid_median = aggregates["best_bid_notional_median"]
        best_ask_median = aggregates["best_ask_notional_median"]
        unwind_p90 = aggregates["unwind_slippage_p90_bps"]

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
                best_bid_notional_pass=criteria.best_bid_notional_pass,
                best_ask_notional_pass=criteria.best_ask_notional_pass,
                unwind_slippage_pass=criteria.unwind_slippage_pass,
                band_10bps_notional_pass=criteria.band_10bps_notional_pass,
                topn_notional_pass=criteria.topn_notional_pass,
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

    export_depth_metrics(out_dir, results, band_bps=depth_cfg.band_bps, logger=logger)
    if candidates and all(isinstance(item, ScoreResult) for item in candidates):
        export_summary_enriched(
            out_dir,
            candidates,
            results,
            band_bps=depth_cfg.band_bps,
            edge_min_bps=cfg.thresholds.edge_min_bps,
            logger=logger,
        )

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
