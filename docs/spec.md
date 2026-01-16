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

The system calculates two edge metrics:

1. **edge_mm_bps** (Maker/Maker mode - primary metric):
   ```
   edge_mm_bps = spread_median_bps - 2 × maker_bps - slippage_buffer_bps
   ```
   This assumes maker fills on both entry and exit (spread-capture strategy).

2. **edge_with_unwind_bps** (Emergency unwind penalty):
   ```
   edge_with_unwind_bps = spread_median_bps - (maker_bps + taker_bps) - slippage_buffer_bps
   ```
   This represents worst-case forced taker exit.

3. **net_edge_bps** (Primary reporting metric):
   ```
   net_edge_bps = edge_mm_bps
   ```
   The net edge uses the maker/maker model as this reflects normal operation.

Defaults:

- `fees.maker_bps = 2.0`
- `fees.taker_bps = 4.0`
- `thresholds.slippage_buffer_bps = 2.0`

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
- `missing_24h_stats` — symbol not found in ticker API response OR parse error (AD-101: API returning `null` for quoteVolume/count is valid, not "missing")
- `no_volume_data` — volume data unavailable (API returned null for both quoteVolume and volume, and estimate couldn't be computed)

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
- `trades_24h`
- `net_edge_bps`
- `pass_spread`
- `score`
- `fail_reasons`
