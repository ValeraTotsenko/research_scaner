# MEXC Spread Feasibility Scanner — Fix Pack (from trader feedback)

Version: v0.1 (2026-01-15)

This file turns `feedback.md` into **Codex Task Briefs** using our Task Brief Standard.

---

## Decision Log (for this fix pack)

- **AD-101**: `missing_24h_stats` must be true *only* when data is truly missing/unparseable. `quoteVolume: null` and `count: null` are valid and must not be treated as missing.
- **AD-102**: When `quoteVolume` is null but base `volume` exists, estimate quote volume via `quoteVolume_est = volume * midPrice`, where `midPrice` is derived from `bookTicker`.
- **AD-103**: Spread filtering must be a **corridor** (min/max) for median and p90.
- **AD-104**: Edge must be separated into `edge_mm_bps` (maker/maker) and `edge_with_unwind_bps` (maker+taker). Pass/shortlist must be based on `edge_mm_bps`.
- **AD-105**: Depth sampling must be feasible under API limits. Implement **Depth Snapshot Mode** (recommended for research) and/or compute effective expected samples from the planner.
- **AD-106**: Depth PASS/FAIL must be an explicit boolean function over explicit metrics, each producing a clear fail reason.
- **AD-107**: Universe must be based on `defaultSymbols` (API-tradable set) with `exchangeInfo` as additional metadata filter.
- **AD-108**: API errors must be standardized: 403/429 => degraded run; 5xx => api_unstable, without converting into “missing data”.

---

# [BUGFIX-001] Correct `missing_24h_stats` + effective 24h quote volume

- **Цель / зачем это нужно**
  Fix incorrect labeling where `missing_24h_stats` is applied broadly, skewing breakdown and filters. Ensure the scanner treats `null` fields correctly and can still filter by liquidity via `quoteVolume_est` when needed.

- **Контекст**
  Universe stage uses `ticker/24hr` and rejects by volume/trades. The trader feedback says `quoteVolume` and `count` may legitimately be `null`.

- **Scope (что делаем)**
  1) Implement correct `missing_24h_stats` logic per AD-101.
  2) Compute `quoteVolume_24h_effective` using raw quoteVolume OR estimation (AD-102).
  3) Ensure `count=null` never triggers missing/reject when `require_trade_count=false`.
  4) Add detailed columns to summary/universe artifacts for transparency.

- **Out of scope**
  - Rewriting entire universe building.
  - Changing existing CLI commands.

## Требования к интерфейсам / контрактам

### New/updated fields in artifacts
- `universe.json` symbol record MUST include:
  - `quoteVolume_24h_raw` (float|null)
  - `volume_24h_raw` (float|null)
  - `mid_price` (float|null)
  - `quoteVolume_24h_est` (float|null)
  - `quoteVolume_24h_effective` (float|null)
  - `missing_24h_stats` (bool)
  - `missing_24h_reason` (string|null; one of `no_row`, `parse_error`, `no_volume_and_no_mid`, `no_any_fields`)

- `universe_rejects.csv` MUST:
  - stop adding `missing_24h_stats` unless truly missing
  - if rejected for missing, include `missing_24h_reason`

### Config behavior
- `universe.use_quote_volume_estimate` (existing) must control whether estimation is allowed.
- If estimation is disabled and quoteVolume is null => effective quote volume becomes null.

## Детальный план реализации

- Files to edit (adjust to your actual repo paths):
  - `scanner/pipeline/universe.py` (or equivalent)
  - `scanner/mexc/client.py` (if needed for additional endpoint)
  - `scanner/models/universe.py` (DTO/model additions)
  - `scanner/io/universe_export.py` (serialize new fields)

- Algorithm:
  1) Fetch `ticker/24hr` once per run.
  2) Build `ticker_map[symbol]` with safe parsing; on parse error mark symbol as `missing_24h_stats=true` with reason `parse_error`.
  3) Fetch `bookTicker` once per run (or reuse existing raw bookTicker capture), compute `mid_price = (bid+ask)/2` if both exist.
  4) For each symbol:
     - read `quoteVolume`, `volume`, `count` from ticker.
     - set `missing_24h_stats=true` only if:
       - no ticker row at all, OR parse error, OR (quoteVolume is null AND volume is null AND mid_price is null)
     - if `quoteVolume` is null AND `volume` not null AND `mid_price` not null AND `use_quote_volume_estimate=true`:
       - compute `quoteVolume_24h_est = volume * mid_price`
     - set `quoteVolume_24h_effective = quoteVolume ?? quoteVolume_24h_est`

## Edge cases
1) quoteVolume=null, volume!=null, mid_price!=null => not missing, effective uses estimate.
2) quoteVolume=null, volume!=null, mid_price=null => missing.
3) quoteVolume!=null => use raw, estimate can remain null.
4) count=null and require_trade_count=false => no reject reason.
5) ticker row exists but volume fields are strings => parse and normalize; if fails => parse_error.

## Observability
- Logs (JSON):
  - `event=ticker24h_parsed` with counts: total_rows, parse_errors
  - `event=quote_volume_estimated` per symbol sampled (or aggregated) with `used_est=true`
- Metrics:
  - counter `ticker24h_parse_error_total`
  - counter `quote_volume_est_used_total`
  - gauge `missing_24h_stats_symbols`

## Тесты
- Unit tests (>=3):
  1) null quoteVolume + volume + mid => missing=false, effective=volume*mid
  2) null quoteVolume + null volume + mid null => missing=true
  3) count null + require_trade_count=false => no missing & no reject
- Failure tests (>=2):
  - parse error in ticker row => missing=true with reason parse_error
  - mid computation with bad bid/ask => mid=null and correct missing evaluation

## Definition of Done
- `missing_24h_stats` no longer equals total symbols by default.
- `universe.json`/`summary.csv` expose raw vs effective volumes.
- Unit tests pass.

## Риски / техдолг
- Estimation depends on mid_price; if bookTicker is stale, add note in docs.

---

# [BUGFIX-002] Universe must be based on `defaultSymbols` (API-tradable set)

- **Цель / зачем**
  Ensure universe includes only symbols officially supported for API trading; avoid scanning pairs that cannot be traded via API or are disabled.

- **Контекст**
  Current universe uses `exchangeInfo` status filter but trader feedback requires `defaultSymbols`.

- **Scope**
  1) Fetch `GET /api/v3/defaultSymbols` and use it as baseline universe set.
  2) Apply `exchangeInfo` as metadata/secondary filter (status, quote asset, etc.).
  3) (Optional) If implemented, exclude delisted symbols using `symbol/offline`.
  4) Add clear reject reasons.

- **Out of scope**
  - Any authenticated endpoints.

## Contracts
- `universe.json` must include flags:
  - `in_default_symbols` (bool)
  - `in_exchange_info` (bool)
  - `exchange_status` (raw)
- `universe_rejects.csv` reasons must include:
  - `not_in_default_symbols`, `missing_exchange_info`, `status_not_allowed`, `wrong_quote_asset`

## Plan
- Files:
  - `scanner/pipeline/universe.py`
  - `scanner/mexc/client.py` (add method `get_default_symbols()`)
- Steps:
  1) `default = set(get_default_symbols())`
  2) `exinfo = get_exchange_info()` => map by symbol
  3) Candidates start from `default` only
  4) For each symbol, require presence in exinfo else reject `missing_exchange_info`
  5) Apply `allowed_exchange_status` and `quote_asset` filters
  6) Apply 24h filters using BUGFIX-001 effective volumes

## Edge cases
1) defaultSymbols API returns empty => fail universe stage with explicit error.
2) symbol in defaultSymbols but missing in exchangeInfo => reject with reason.
3) exchange status unexpected => reject with `status_unexpected` and log warning.
4) case sensitivity: normalize symbols to uppercase consistently.
5) if whitelist provided => whitelist intersects defaultSymbols (never expands).

## Observability
- Log `event=default_symbols_loaded` (count)
- Metrics: `default_symbols_total`, `universe_not_in_default_symbols_total`

## Tests
- Unit:
  1) whitelist cannot add symbols not in defaultSymbols
  2) symbol missing in exchangeInfo => reject reason correct
  3) status filter uses allowed list

## DoD
- Universe count matches `defaultSymbols` intersection rules.
- Reject reasons reflect reality.

---

# [BUGFIX-003] Spread thresholds must be a corridor (min/max for median and p90)

- **Цель / зачем**
  Avoid selecting ultra-tight spreads with no edge after fees/buffers, and avoid chaotic wide spreads.

- **Контекст**
  Spread stage currently uses only `*_max_bps`.

- **Scope**
  1) Add config fields: `median_min_bps`, `p90_min_bps`.
  2) Update spread pass/fail logic.
  3) Update fail reasons and reporting.

- **Out of scope**
  - Changing how spread is measured (median/p90 computation itself).

## Contracts
- Config:
  ```yaml
  thresholds:
    spread:
      median_min_bps: 8.0
      median_max_bps: 15.0
      p90_min_bps: 0.0
      p90_max_bps: 45.0
  ```

- `summary.csv` / stage artifacts must include:
  - `spread_median_bps`, `spread_p90_bps`
  - `spread_pass` (bool)
  - `spread_fail_reasons` (string list or `;`-joined string)

## Plan
- Files:
  - `scanner/pipeline/spread_check.py`
  - `scanner/config/schema.py` (or validator)
  - `scanner/io/spread_export.py`
- Steps:
  1) Extend config model + validation (min <= max; non-negative)
  2) Spread PASS requires:
     - `median_min_bps <= median <= median_max_bps`
     - `p90_min_bps <= p90 <= p90_max_bps`
  3) Generate fail reasons:
     - `spread_median_low/high`, `spread_p90_low/high`

## Edge cases
1) Any of the computed metrics missing/NaN => fail with `spread_metrics_missing`.
2) User sets min=0 => corridor degenerates to old behavior.
3) p90 < median (should not happen, but if it does) => log warning and still apply corridor.

## Observability
- Metric: `spread_pass_total`, `spread_fail_total` by reason (tag)
- Log `event=spread_thresholds_applied` with corridor values

## Tests
- Unit:
  1) median below min => fail with reason
  2) median above max => fail
  3) within corridor => pass

## DoD
- PASS_SPREAD excludes too-tight and too-wide spreads by corridor.

---

# [BUGFIX-004] Split edge metrics and filter shortlist by `edge_mm_bps`

- **Цель / зачем**
  Current `net_edge_bps` mixes trading modes and misleads selection. Need two metrics and selection based on maker/maker edge.

- **Контекст**
  Trader concept: baseline is maker/maker; taker is emergency unwind.

- **Scope**
  1) Implement:
     - `edge_mm_bps = spread_median_bps - 2*maker_bps - slippage_buffer_bps`
     - `edge_with_unwind_bps = spread_median_bps - (maker_bps + taker_bps) - slippage_buffer_bps`
  2) Add `thresholds.edge_min_bps` and `thresholds.slippage_buffer_bps`.
  3) Define PASS_TOTAL/shortlist based on `edge_mm_bps >= edge_min_bps`.
  4) Keep `edge_with_unwind_bps` as risk indicator (report-only).

- **Out of scope**
  - Any slippage modeling beyond a single buffer parameter.

## Contracts
- Config additions:
  ```yaml
  thresholds:
    edge_min_bps: 3.0
    slippage_buffer_bps: 2.0
  fees:
    maker_bps: 2.0
    taker_bps: 4.0
  ```

- `summary.csv` must include:
  - `edge_mm_bps`, `edge_with_unwind_bps`
  - `edge_pass` (bool)
  - `edge_fail_reason` (e.g., `edge_mm_below_min`)

## Plan
- Files:
  - `scanner/analytics/score.py` (or wherever net_edge is computed)
  - `scanner/pipeline/score_stage.py`
  - `scanner/io/score_export.py`
- Steps:
  1) Implement formulas and store results per symbol.
  2) Ensure units are consistent (bps).
  3) Selection:
     - Either in score stage (preferred) mark `edge_pass`
     - Or in report stage compute pass list from score results.

## Edge cases
1) spread_median missing => edge metrics missing => fail.
2) user sets negative buffer => validation error.
3) maker/taker bps 0 => allowed, but warn in docs.

## Observability
- Metric: `edge_pass_total`
- Log: `event=edge_computed` (can be aggregated) with min/mean/max

## Tests
- Unit:
  1) Verify edge_mm formula
  2) Verify edge_with_unwind formula
  3) Verify pass threshold logic

## DoD
- Report contains both edge metrics.
- Shortlist/PASS_TOTAL depends on `edge_mm_bps` only.

---

# [BUGFIX-005] Depth sampling feasibility under API limits: add Snapshot Mode + planner-aware expected samples

- **Цель / зачем**
  Current depth uptime logic can be mathematically impossible with given `candidates_limit`, `duration`, `interval`, and `max_rps`. This yields systematic false failures.

- **Контекст**
  Feedback provides three options; implement at least Variant A (Snapshot Mode) and (optionally) Variant B (planner-aware expected samples).

- **Scope**
  1) Add `depth.mode: snapshot | timeseries`.
  2) Snapshot Mode:
     - For each candidate, take `snapshots_per_symbol` snapshots (1..N) and compute depth metrics from them.
     - Do NOT apply `uptime_min` to depth in snapshot mode.
     - Instead, track `snapshot_success_ratio`.
  3) Timeseries Mode:
     - Compute `expected_samples_per_symbol_effective` from the scheduler, not from `duration/interval` naive formula.
     - Do not fail uptime if planner cannot physically meet expectations.

- **Out of scope**
  - Implementing parallelism.

## Contracts
- Config additions:
  ```yaml
  depth:
    mode: snapshot
    snapshots_per_symbol: 2
  sampling:
    depth:
      candidates_limit: 80
  ```

- Artifacts:
  - `depth_metrics.csv` must include:
    - `sample_count`
    - `snapshot_success_ratio` (0..1)
    - `expected_samples_per_symbol_effective` (timeseries only; else empty)

## Plan
- Files:
  - `scanner/pipeline/depth_check.py`
  - `scanner/analytics/depth_metrics.py`
  - `scanner/config/schema.py`
- Steps:
  1) Add config parsing/validation.
  2) Implement two execution paths:
     - snapshot: iterate candidates, request depth N times with small pauses honoring max_rps
     - timeseries: existing loop but expected samples computed from schedule
  3) Ensure stage timeout behavior still works; snapshot mode should be designed to return partial results on timeout.

## Edge cases
1) API 429 => backoff; snapshot_success_ratio drops but stage doesn’t mislabel as missing.
2) snapshots_per_symbol <= 0 => validation error.
3) mode unknown => validation error.
4) stage timeout hits mid-loop => return partial results if configured.

## Observability
- Metrics:
  - `depth_snapshot_requests_total`
  - `depth_snapshot_success_total`
  - `depth_samples_per_symbol_hist`
- Log: `event=depth_mode_selected` with mode and snapshots_per_symbol.

## Tests
- Unit:
  1) snapshot mode does not apply uptime thresholds
  2) snapshot mode sample_count equals snapshots_per_symbol for fully successful
  3) timeseries expected samples derived from planner inputs
- Failure:
  - simulated rate limit reduces success ratio but does not cause incorrect uptime fail

## DoD
- With snapshot mode and limited RPS, depth stage yields stable metrics without artificial uptime failures.

---

# [BUGFIX-006] Make Depth PASS/FAIL explicit and transparent (criteria + reasons)

- **Цель / зачем**
  Depth stage must be a transparent boolean function from explicit metrics, with a clear fail reason for each violated criterion.

- **Контекст**
  Currently some configured metrics (band/topN) are either not used or used implicitly.

- **Scope**
  1) Define MVP depth pass criteria (always on):
     - `best_bid_notional_median >= best_level_min_notional`
     - `best_ask_notional_median >= best_level_min_notional`
     - `unwind_slippage_p90_bps <= unwind_slippage_max_bps`
  2) Add optional criteria toggles:
     - band check (10 bps)
     - topN check
  3) Ensure every criterion produces a unique fail reason.

- **Out of scope**
  - Changing how best-level notional or slippage are computed (unless needed for correctness).

## Contracts
- Config:
  ```yaml
  thresholds:
    depth:
      best_level_min_notional: 500
      unwind_slippage_max_bps: 25
      band_10bps_min_notional: 1000     # optional
      topN_min_notional: 5000           # optional
  depth:
    enable_band_checks: true
    enable_topN_checks: false
  ```

- Artifacts:
  - `summary_enriched.csv` must include:
    - `depth_pass` (bool)
    - `depth_fail_reasons` (list or string)

## Plan
- Files:
  - `scanner/pipeline/depth_check.py` (decision logic)
  - `scanner/io/depth_export.py` (export fail reasons)
  - `scanner/config/schema.py` (new toggles & thresholds)
- Steps:
  1) Implement `evaluate_depth_pass(metrics, config) -> (bool, reasons[])`.
  2) Append reasons:
     - `best_bid_notional_low`, `best_ask_notional_low`, `unwind_slippage_high`, `band_10bps_notional_low`, `topN_notional_low`, `depth_metrics_missing`
  3) Ensure PASS matches the boolean AND of enabled criteria.

## Edge cases
1) band metrics missing => if band checks enabled => fail `band_metrics_missing`.
2) snapshot mode with low sample_count => metrics still computed; if not computable => `depth_metrics_missing`.
3) topN disabled => never adds topN reasons.

## Observability
- Metrics:
  - `depth_pass_total`
  - `depth_fail_total` (tag by reason)
- Log: `event=depth_pass_evaluated` (counts per reason)

## Tests
- Unit:
  1) each criterion individually triggers its reason
  2) disabled criteria do not affect pass
  3) missing metrics => fail with `depth_metrics_missing`

## DoD
- For any symbol, the report shows exactly why it failed depth.

---

# [BUGFIX-007] Standardize API error handling and run health classification (403/429/5xx)

- **Цель / зачем**
  Prevent API transient errors from being misinterpreted as missing market data and contaminating statistics. Provide a clear run health signal.

- **Контекст**
  Requirements:
  - 403 => WAF limit
  - 429 => rate limit
  - 5xx => server-side; do not treat as “operation failed” or “data missing”; mark as unstable

- **Scope**
  1) Introduce run-level health classification: `ok | degraded | api_unstable`.
  2) HTTP client must classify responses:
     - 429: backoff + mark degraded
     - 403: backoff + mark degraded (WAF)
     - 5xx: limited retry, mark api_unstable; do NOT convert to missing stats
  3) Expose run health summary in `run_meta.json` and in report.

- **Out of scope**
  - Building a full adaptive rate limiter.

## Contracts
- `run_meta.json` must include:
  - `run_health` (string enum)
  - `api_error_counts`: {`http_403`, `http_429`, `http_5xx`}
  - `rate_limited` (bool)
  - `waf_limited` (bool)

- Metrics:
  - counters: `http_403_total`, `http_429_total`, `http_5xx_total`
  - gauge: `run_degraded` (0/1), `run_api_unstable` (0/1)

## Plan
- Files:
  - `scanner/mexc/http_client.py` (or wherever request is made)
  - `scanner/runtime/run_meta.py`
  - `scanner/report/report_builder.py`
- Steps:
  1) Centralize error classification in one function: `classify_http(status_code) -> category`.
  2) Implement behavior:
     - 429: respect `Retry-After` if present; else exponential backoff
     - 403: exponential backoff; reduce RPS if dynamic RPS control exists; else log guidance
     - 5xx: limited retries; mark api_unstable
  3) Ensure caller logic receives a typed error and does not treat it as “missing data”; instead records `api_unstable` flags.

## Edge cases
1) burst of 429 => degraded; run continues with slower tempo
2) repeated 403 => degraded; run may fail-fast if too many consecutive
3) intermittent 5xx => api_unstable; do not alter missing_24h_stats
4) mixed errors => run_health priority: api_unstable > degraded > ok

## Observability
- Logs:
  - `event=api_rate_limited` (status=429, endpoint, attempt, backoff_s)
  - `event=api_waf_limited` (status=403, ...)
  - `event=api_server_error` (status=5xx, ...)
- Metrics as above.

## Tests
- Unit:
  1) 429 classification => degraded
  2) 403 classification => degraded
  3) 5xx classification => api_unstable
- Integration (mock HTTP):
  - simulate 429 with Retry-After; verify wait and retry count

## DoD
- Reports include run health summary.
- API errors do not inflate “missing” stats.

---

## Suggested implementation order
1) BUGFIX-001 + BUGFIX-002 (universe correctness)
2) BUGFIX-003 + BUGFIX-004 (spread & edge correctness)
3) BUGFIX-005 + BUGFIX-006 (depth correctness)
4) BUGFIX-007 (trustworthy results under API issues)

