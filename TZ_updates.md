## Scanner Spec v0.1 — Metrics, Formulas, Report Format (Update)

### 0) Data Sources (MEXC REST)
- /api/v3/exchangeInfo (universe базовый список символов, статусы, precision/мин. ордера и т.д.)
- /api/v3/ticker/24hr (ALL symbols) — Weight(IP)=40; важное: quoteVolume и count могут быть null (это не ошибка)  :contentReference[oaicite:2]{index=2}
- /api/v3/ticker/bookTicker (ALL symbols) — Weight(IP)=1  :contentReference[oaicite:3]{index=3}
- /api/v3/depth?symbol=...&limit=50 — max limit 5000; Weight(IP) указан как 1 в market-data docs, но встречается таблица с weight=10 (проектировать консервативно)  :contentReference[oaicite:4]{index=4}
- Rate limits: у spot-v3 есть независимые лимиты по IP/UID, типичный лимит "500 per 10 seconds" на endpoint (см General Info)  :contentReference[oaicite:5]{index=5}

### 1) Universe Stage (Symbols filter)
Input: exchangeInfo + 24hr tickers
Фильтры:
- quoteAsset == <quote_asset> (например USDT)
- symbol status разрешённый (allowed_exchange_status)
- min_quote_volume_24h:
  - если ticker.quoteVolume != null: use quoteVolume
  - если quoteVolume == null: рассчитывать estimate_quote_volume_24h = volume_base_24h * mid_price
    где mid_price = (bidPrice + askPrice)/2 из bookTicker, либо lastPrice если mid недоступен
- trade count:
  - если require_trade_count == true:
      - если ticker.count == null -> FAIL reason: trade_count_missing
      - иначе если count < min_trades_24h -> FAIL: trades_low
  - если require_trade_count == false:
      - отсутствие ticker.count НЕ является fail reason
      - при желании фиксировать info-flag: trade_count_unknown=true (но не FAIL)

Blacklist/Whitelist:
- blacklist_regex применяется на symbol (например "ONUSDT$" чтобы исключить нецелевой класс)
- whitelist если задан — оставляем только его

Artifacts:
- universe.json (kept symbols + их базовые атрибуты)
- universe_rejects.csv (symbol + reject_reason + ключевые поля)

### 2) Spread Sampling Stage (REST/WS independent)
Источник: bookTicker ALL symbols (bidPrice, bidQty, askPrice, askQty)  :contentReference[oaicite:6]{index=6}
Параметры: duration_s, interval_s, min_uptime

Для каждого тика и символа:
- validate quote:
  - bidPrice>0, askPrice>0, askPrice >= bidPrice (иначе invalid_quote++)
  - если missing — missing_quote++
- mid = (askPrice + bidPrice)/2
- spread_bps = ((askPrice - bidPrice) / mid) * 10_000

По каждому symbol агрегировать:
- spread_p10_bps, spread_p25_bps, spread_median_bps, spread_p90_bps
- uptime = valid_ticks / expected_ticks
  expected_ticks = floor(duration_s / interval_s)
  (должно соответствовать ticks_total из pipeline_state)

Spread thresholds (ВАЖНО: коридор, а не только max):
- median_min_bps <= spread_median_bps <= median_max_bps
- p90_min_bps <= spread_p90_bps <= p90_max_bps (обычно только верхняя граница)

Fail reasons (Spread):
- invalid_quote_rate_high (если invalid_quotes/expected_ticks > X)
- uptime_low
- spread_median_low / spread_median_high
- spread_p90_low / spread_p90_high

Artifacts:
- raw_bookticker.jsonl[.gz] (по желанию)
- summary.csv (обязателен; см формат ниже)

### 3) Edge Metrics (экономика)
Комиссии берём из config.fees:
- maker_bps
- taker_bps
Также ввести buffer_bps (консервативный запас на микро-скольжение/очередь), default 1–3 bps.

Определить 2 edge-метрики:
- edge_mm_bps = spread_median_bps - 2*maker_bps - buffer_bps
- edge_mt_bps = spread_median_bps - (maker_bps + taker_bps) - buffer_bps

(Опционально) pessimistic:
- edge_mm_p25_bps = spread_p25_bps - 2*maker_bps - buffer_bps

В summary.csv хранить все edge поля. В scoring по умолчанию использовать edge_mm_*.

### 4) Candidate Selection (для depth)
- candidates_limit = N
- выбирать кандидатов из PASS_SPREAD по score (или по edge_mm_p25_bps), затем взять top-N

Важно:
- отчёт обязан показывать N (сколько реально отправили в depth)

### 5) Depth Stage
Источник: /api/v3/depth?symbol=...&limit=50  :contentReference[oaicite:7]{index=7}
Режимы:
A) Snapshot-mode (рекомендуемый для массовых прогонов):
- на symbol делаем K снимков (K=1..2), пауза interval_s между ними
B) Time-series:
- делаем регулярные снимки весь duration_s

Метрики для одного snapshot:
- best_bid_notional = bidPrice_1 * bidQty_1
- best_ask_notional = askPrice_1 * askQty_1
- topn_bid_notional = Σ_{i=1..top_n_levels} (bidPrice_i * bidQty_i)
- topn_ask_notional = Σ_{i=1..top_n_levels} (askPrice_i * askQty_i)

Band notional (пример для bid, band=10 bps):
- bid_price_floor = best_bid_price * (1 - band_bps/10_000)
- band_bid_notional_10bps = Σ (price_i*qty_i) для bid уровней, где price_i >= bid_price_floor
Аналогично для ask с потолком.

Unwind slippage (stress_notional_usdt):
- mid = (best_bid_price + best_ask_price)/2
- simulate SELL notional S into bids:
  - пройти bids сверху вниз, набирать base_qty_i до достижения S по notional
  - vwap_sell = Σ(price_i*qty_i_filled) / Σ(qty_i_filled)
  - slippage_sell_bps = ((mid - vwap_sell)/mid)*10_000
- simulate BUY notional S into asks:
  - vwap_buy ...
  - slippage_buy_bps = ((vwap_buy - mid)/mid)*10_000
- unwind_slippage_bps = max(slippage_sell_bps, slippage_buy_bps)

По symbol агрегировать по K snapshots:
- unwind_slippage_p90_bps (p90)
- best_*_median_notional (median)
- sample_count, valid_samples, uptime:
  - Snapshot-mode: expected_samples = K
  - Time-series: expected_samples = floor(duration_s / interval_s)  (или строго по планировщику)
  - uptime = valid_samples / expected_samples

Depth thresholds:
- best_level_min_notional: best_bid_notional_median >= X AND best_ask_notional_median >= X
- unwind_slippage_max_bps: unwind_slippage_p90_bps <= Y
- (опционально) uptime_min применяется ТОЛЬКО если uptime корректно определён

Fail reasons (Depth):
- best_bid_notional_low
- best_ask_notional_low
- unwind_slippage_high
- depth_uptime_low (только если осмысленно)
- symbol_unavailable / empty_book / invalid_book

Artifacts:
- depth_metrics.csv (ТОЛЬКО по кандидатам N, не по всему universe)
- summary_enriched.csv (left join summary + depth metrics + pass_depth/pass_total)

### 6) Scoring (общее)
Рекомендуемая базовая модель:
- score = w1 * edge_mm_p25_bps + w2 * clip(best_level_notional_median) - w3 * unwind_slippage_p90_bps - w4 * penalties
Где penalties включают:
- если symbol из blacklist class -> -inf (либо exclude)
- если uptime_low -> штраф

ВАЖНО: PASS_TOTAL = PASS_SPREAD && PASS_DEPTH

### 7) Report.md — обязательный формат (чтобы не было вводящих в заблуждение отчётов)
Разделы:
1) Run meta: run_id, started_at, report_at, scanner_version, git_commit, config_hash
2) Parameters (печатать РЕАЛЬНЫЕ значения из config):
   - spread: duration_s, interval_s, min_uptime
   - depth: mode (snapshot/time-series), duration_s, interval_s, limit, candidates_limit, effective_weight_assumption
   - thresholds: все spread min/max, depth thresholds, buffer_bps, fees
3) Universe stats:
   - total symbols in exchangeInfo
   - kept, rejected
   - reject breakdown (top reasons)
4) Spread stats:
   - scanned, pass_spread, fail_spread
   - quantiles table по spread_median_bps и spread_p90_bps (по pass_spread universe)
5) Depth results:
   - depth_candidates_requested = candidates_limit
   - depth_candidates_actual = rows(depth_metrics.csv)
   - stage status: success/timeout + has_minimum_data + elapsed_s
   - pass_depth count (из candidates), pass_total count
   - depth uptime p50 (только если корректно определён)
6) Top candidates table (top_n):
   - symbol, score, spread_median_bps, spread_p90_bps
   - edge_mm_p25_bps, edge_mm_bps
   - best_bid/ask_notional_median, unwind_slippage_p90_bps
   - pass flags + key fail reasons (если нет)
7) Fail reason breakdown:
   - Spread reasons (без "missing_24h_stats" мусора)
   - Depth reasons (только по кандидатам)
8) Notes:
   - предупреждение если stage timeout
   - предупреждение если depth uptime некорректен/режим snapshot

CSV formats:
- summary.csv (минимум колонки):
  symbol,
  quoteVolume_24h, quoteVolume_est_24h, used_quote_volume_estimate(bool),
  trades_24h, trade_count_missing(bool),
  spread_p10_bps, spread_p25_bps, spread_median_bps, spread_p90_bps, uptime,
  edge_mm_bps, edge_mm_p25_bps, edge_mt_bps,
  pass_spread, score, fail_reasons
- depth_metrics.csv:
  symbol, sample_count, valid_samples, uptime,
  best_bid_notional_median, best_ask_notional_median,
  topn_bid_notional_median, topn_ask_notional_median,
  band_*,
  unwind_slippage_p90_bps,
  pass_depth, fail_reasons
- summary_enriched.csv:
  summary + depth_metrics joined + pass_total
