# SCAN-005 Stats, Scoring, PASS/FAIL, Summary Export

## Universe filters (24h notional)

Universe selection uses USDT-notional 24h volume and optional trade count filters.

- `min_quote_volume_24h` is interpreted as USDT notional volume.
- If `quoteVolume` is missing and `use_quote_volume_estimate = true`, estimate
  `quoteVolume_est = volume * lastPrice`.
- If both `quoteVolume` and estimated notional are unavailable, the symbol is rejected.
- `min_trades_24h` is applied only when `count` is present or when
  `require_trade_count = true`.

## Spread stats inputs

Each spread sample provides `bid` and `ask` for a single symbol at a sampling tick.

### Validity rules

A sample is **valid** when:

- `bid > 0`
- `ask > 0`
- `mid = (bid + ask) / 2 > 0`

Invalid samples are counted as `invalid_quotes` and excluded from quantiles.

### Spread formula

- `spread_bps = (ask - bid) / mid * 10_000`

### Quantiles

For the sorted spread series:

- `spread_median_bps` uses the standard median.
- `spread_p10_bps`, `spread_p25_bps`, `spread_p90_bps` use linear interpolation with
  `pos = p * (n - 1)`.

### Uptime

- `uptime = valid_samples / total_samples`

### Insufficient samples

- `insufficient_samples = valid_samples < 3`

## Fees and edge metrics

The system calculates three edge metrics:

1. **edge_mm_bps** (Maker/Maker mode - optimistic):
   ```
   edge_mm_bps = spread_median_bps - 2 × maker_bps - buffer_bps
   ```
   This assumes maker fills on both entry and exit (spread-capture strategy) using median spread.

2. **edge_mm_p25_bps** (Maker/Maker mode - pessimistic):
   ```
   edge_mm_p25_bps = spread_p25_bps - 2 × maker_bps - buffer_bps
   ```
   This uses the 25th percentile spread for a more conservative estimate of maker/maker edge.

3. **edge_mt_bps** (Maker/Taker mode - emergency unwind):
   ```
   edge_mt_bps = spread_median_bps - (maker_bps + taker_bps) - buffer_bps
   ```
   This represents worst-case forced taker exit scenario.

4. **net_edge_bps** (Primary reporting metric):
   ```
   net_edge_bps = edge_mm_bps
   ```
   The net edge uses the optimistic maker/maker model as this reflects normal operation.

Defaults:

- `fees.maker_bps = 2.0`
- `fees.taker_bps = 4.0`
- `thresholds.buffer_bps = 2.0` (formerly `slippage_buffer_bps`)

## PASS/FAIL (PASS_SPREAD)

`PASS_SPREAD` is **true** when all conditions hold:

- `insufficient_samples` is false
- `invalid_quotes == 0`
- `uptime >= thresholds.uptime_min`
- `spread_median_bps <= thresholds.spread.median_max_bps`
- `spread_p90_bps <= thresholds.spread.p90_max_bps`

Defaults:

- `thresholds.spread.median_max_bps = 25`
- `thresholds.spread.p90_max_bps = 60`
- `thresholds.uptime_min = 0.90`

### Fail reasons

- `insufficient_samples` — too few valid samples
- `invalid_quotes` — zero/negative prices in samples
- `low_uptime` — uptime below threshold
- `spread_median_low` — median below min threshold
- `spread_median_high` — median above max threshold
- `spread_p90_low` — p90 below min threshold
- `spread_p90_high` — p90 above max threshold
- `no_volume_data` — volume data unavailable (API returned null for both quoteVolume and volume, and estimate couldn't be computed)

### Informational flags (NOT fail reasons)

- `missing_24h_stats` — symbol not found in ticker API response OR parse error. This is
  informational only and NOT added to fail_reasons. Symbols with truly missing data are
  filtered in the universe stage. Per AD-101, API returning `null` for quoteVolume/count
  is valid, not "missing".

## Score

```
score = max(net_edge_bps, 0) + uptime * 100 - max(spread_p90_bps - spread_p10_bps, 0)
```

If `net_edge_bps` is unavailable, the `max(net_edge_bps, 0)` term is treated as `0`.

### Sorting

When sorting results, use `score` descending with a secondary key of `symbol` ascending
for stable ties.

## Summary exports

`summary.csv` and `summary.json` have one row/object per symbol with at least:

- `symbol`
- `spread_median_bps`
- `spread_p25_bps`
- `spread_p10_bps`
- `spread_p90_bps`
- `uptime`
- `quoteVolume_24h`
- `quoteVolume_24h_raw`
- `volume_24h_raw`
- `mid_price`
- `quoteVolume_24h_est`
- `quoteVolume_24h_effective`
- `used_quote_volume_estimate` (boolean: true if estimate was used instead of raw)
- `trades_24h`
- `trade_count_missing` (boolean: true if trades_24h is None)
- `edge_mm_bps`
- `edge_mm_p25_bps` (new: pessimistic maker/maker edge)
- `edge_mt_bps` (new: maker/taker edge, formerly edge_with_unwind_bps)
- `net_edge_bps`
- `pass_spread`
- `score`
- `fail_reasons`

## Depth stage

### Candidate selection

Depth candidates are selected from symbols that passed spread criteria, sorted by
score descending, limited by `candidates_limit`.

### Depth uptime calculation

Depth uptime is calculated as `valid_samples / target_ticks`, where `target_ticks`
accounts for API rate limiting:

```
tick_duration_s = num_symbols / max_rps
if tick_duration_s > interval_s:
    # Effective snapshot mode
    target_ticks = duration_s / tick_duration_s
else:
    target_ticks = duration_s / interval_s
```

Example: 80 symbols at 2 RPS with duration=1200s, interval=30s:
- tick_duration = 80/2 = 40s (exceeds 30s interval)
- effective_target = 1200/40 = 30 ticks (not naive 1200/30 = 40)
- uptime p50 of 0.67 means ~20 valid samples per symbol

**Important:** Depth uptime is informational only — NOT a pass/fail criterion.
This differs from spread uptime which IS a pass/fail criterion.

### PASS_DEPTH criteria

A symbol passes depth check when ALL of these conditions hold:

- `best_bid_notional_median >= best_level_min_notional`
- `best_ask_notional_median >= best_level_min_notional`
- `unwind_slippage_p90_bps <= unwind_slippage_max_bps`

### Depth fail reasons

- `missing_best_bid_notional` — no bid data available
- `best_bid_notional_low` — best bid notional below threshold
- `missing_best_ask_notional` — no ask data available
- `best_ask_notional_low` — best ask notional below threshold
- `missing_unwind_slippage` — no slippage data available
- `unwind_slippage_high` — slippage exceeds threshold
- `empty_book` — order book was empty
- `invalid_book_levels` — order book data invalid
- `symbol_unavailable` — symbol not available via API
- `no_valid_samples` — no valid depth samples collected
