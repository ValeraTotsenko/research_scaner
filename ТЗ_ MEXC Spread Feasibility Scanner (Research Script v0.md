# **ТЗ: MEXC Spread Feasibility Scanner (Research Script v0.1)**

## **1\) Цель**

Скрипт должен ответить на вопрос:

“Сколько spot-пар (в заданном universe, например USDT) имеют **устойчивый спред**, достаточный для **покрытия комиссий** и допущений по исполнению (проскальзывание/частичные fill), и при этом имеют минимальную ликвидность, чтобы работать малыми объёмами без сильного влияния?”

Результаты нужны для решения: **имеет ли смысл запускать MVP spread-capture на MEXC**, и какие пары брать первыми.

---

## **2\) Ограничения (как в нашем MVP)**

* **REST-only** (никаких WS стаканов по всем монетам).

* Исследуем только **Spot**.

* Universe по умолчанию: **quoteAsset \= USDT**.

* Итог: **shortlist 20**, но скрипт должен уметь оценить весь universe (например топ-100/топ-300 по объёму), а затем выбрать 20 лучших по скорингу.

---

## **3\) Источники данных (только публичные REST)**

Обязательные endpoints (Spot v3):

1. **ExchangeInfo (список символов, правила/статусы)**: `GET /api/v3/exchangeInfo`

2. **Top-of-book (best bid/ask)**: `GET /api/v3/ticker/bookTicker`

3. **24h статистика**: `GET /api/v3/ticker/24hr`

4. (Опционально, для sanity-check “есть ли объём на лучшей цене”) **order book snapshot**: `GET /api/v3/depth` (в доках market-data он есть; применяется аккуратно из\-за нагрузки)

5. Для валидации доступных пар под API-торговлю: `GET /api/v3/defaultSymbols` (MEXC прямо рекомендует им проверять доступные пары)

Rate limit/частоты для публичных API MEXC описаны в их “term definitions”, где перечислены `ticker/bookTicker`, `ticker/24hr`, `exchangeInfo` и лимиты.

---

## **4\) Входные параметры (CLI / config file)**

Скрипт должен принимать конфиг (yaml/json) или CLI параметры:

### **Universe / фильтры**

* `quote_asset` (default: `USDT`)

* `symbol_whitelist` (optional)

* `symbol_blacklist` (optional)

* `min_24h_quote_volume_usdt` (например 200\_000)

* `min_24h_trades` (например 1\_000) — если метрика доступна в 24hr

* `max_price` / `min_price` (optional)

* `exclude_patterns` (optional: leveraged tokens, test pairs и т.п.)

### **Sampling**

* `sample_duration_min` (default: 30\)

* `sample_interval_sec` (default: 3–10; зависит от rate limit)

* `bookticker_batch_mode` (true/false) — если endpoint позволяет batch (если нет — single)

* `parallelism` (кол-во параллельных запросов, но ограничено rate limit)

### **“Рабочий спред” (пороговые параметры)**

* `min_spread_bps_median` (например 8 bps)

* `min_spread_bps_p25` (например 5 bps)

* `min_edge_after_fees_bps` (например 2 bps)

* `maker_fee_bps_assumption` (например 2 bps) — временно, пока не тянем реальные комиссии (для research достаточно)

* `slippage_buffer_bps` (например 1–3 bps)

### **Output**

* `output_dir`

* `export_csv` (true)

* `export_json` (true)

* `render_markdown_report` (true)

---

## **5\) Что именно считаем (формулы)**

На каждом сэмпле для символа берём `bid`, `ask`:

* `mid = (bid + ask) / 2`

* `spread_abs = ask - bid`

* `spread_bps = (ask - bid) / mid * 10_000`

Эти значения сохраняются во временной ряд.

### **Устойчивость спреда (за 30 минут)**

Считаем статистики:

* `spread_bps_median`

* `spread_bps_p25`, `spread_bps_p10`

* `spread_bps_p90` (как индикатор “разрыва/хаоса”)

* `spread_uptime_pct` \= доля сэмплов, где `spread_bps >= min_spread_bps_floor` (например 5 bps)

### **Экономика после комиссий (грубая)**

Для spread-capture на 2 стороны (buy+sell) минимальная грубая оценка:

* `gross_capture_bps ≈ spread_bps_median`

* `fees_bps ≈ 2 * maker_fee_bps_assumption`

* `net_edge_bps = gross_capture_bps - fees_bps - slippage_buffer_bps`

Критерий “проходит экономику”:

* `net_edge_bps >= min_edge_after_fees_bps`

### **Ликвидность-прокси (из 24hr)**

Из `ticker/24hr` берём:

* `quoteVolume` (объём в USDT за 24h) — основной фильтр

* (если есть) `count` (кол-во сделок) или аналог

---

## **6\) Алгоритм работы (шаги)**

### **Шаг 0 — Инициализация**

* Считать конфиг

* Создать папку результатов, лог-файл, записать параметры запуска и версию скрипта.

### **Шаг 1 — Сформировать universe символов**

1. `exchangeInfo` → список символов.

2. Отфильтровать по:

   * `quoteAsset == USDT`

   * статус торговли (если поле есть, типа `TRADING`)

3. `defaultSymbols` → пересечь с “доступными для spot trading” (как рекомендует MEXC).

4. `ticker/24hr` → применить фильтры по объёму/активности.

Результат: `universe_filtered`.

### **Шаг 2 — Сбор top-of-book спредов (30 минут)**

Цикл на `sample_duration_min`:

* Для каждого символа получить `bookTicker` (best bid/ask).

* Посчитать `spread_bps` и записать (timestamp, symbol, bid, ask, spread\_bps).

* Уважать rate limits и делать backoff при ошибках.

Важно: sampling должен быть **одинаковым по всем символам** (насколько возможно), иначе статистики будут смещены.

### **Шаг 3 — Пост-обработка и скоринг**

Для каждого символа:

* посчитать статистики spread (median/p25/p10/uptime)

* взять 24h quoteVolume, trades count (если есть)

* посчитать `net_edge_bps`

* сформировать `score` (прозрачная формула), например:

`score =`  
  `w1 * clamp(net_edge_bps, 0..X) +`  
  `w2 * log10(quoteVolume) +`  
  `w3 * uptime_pct -`  
  `w4 * spread_volatility_penalty(p90-p10)`

### **Шаг 4 — Выходные артефакты**

1. **CSV**: по каждому символу одна строка (summary features \+ score \+ PASS/FAIL).

2. **Markdown Report**: человекочитаемый отчёт:

   * сколько символов было в exchangeInfo

   * сколько осталось после defaultSymbols/volume фильтра

   * сколько прошло “workable spread” критерии

   * топ-20 по score

   * распределение spread\_median (квантили)

3. **JSON**: raw stats \+ параметры запуска (для дальнейшего сравнения прогонов).

---

## **7\) Критерии PASS/FAIL (“подходит для стратегии”)**

Символ получает `PASS`, если одновременно:

* `quoteVolume_24h >= min_24h_quote_volume_usdt`

* `spread_bps_median >= min_spread_bps_median`

* `spread_bps_p25 >= min_spread_bps_p25` (устойчивость)

* `net_edge_bps >= min_edge_after_fees_bps`

Иначе `FAIL` \+ причина (например: LOW\_VOLUME, LOW\_SPREAD, UNSTABLE\_SPREAD, NEGATIVE\_EDGE).

---

## **8\) Rate limit / устойчивость / ошибки (обязательно)**

### **Требования**

* Троттлинг запросов с учётом лимитов (см. MEXC term definitions)

* При ошибках:

  * 429 → exponential backoff \+ запись инцидента в лог

  * 5xx → retry ограниченное число раз, но без “дудоса”

* Логи: count запросов/мин, ошибки по типам, фактическая частота sampling.

### **Логирование (минимум)**

* INFO: старт/конфиг/размер universe

* WARN: пропуски символов/невалидные ответы

* ERROR: массовые сбои REST

---

## **9\) Acceptance Criteria (что считаем “готово”)**

Скрипт считается выполненным, если:

1. Запускается одной командой и сам создаёт `output_dir`.

2. За 30 минут собирает спреды минимум по N символам (N задаётся фильтрами).

3. Отдаёт:

   * `summary.csv`

   * `report.md`

   * `run_meta.json`

4. В отчёте есть:

   * число PASS символов

   * топ-20 shortlist

   * квантили по spread\_median

   * список причин FAIL (агрегация)

5. Умеет повторять прогон и сравнивать runs (хотя бы по timestamp/параметрам).

---

## **10\) Рекомендуемые дефолты для первого прогона (разумно для MEXC REST)**

(Дефолты можно менять, но для старта они дадут понятную картину.)

* `sample_duration_min = 30`

* `sample_interval_sec = 5` (меньше — риск упереться в лимиты при большом universe)

* `min_24h_quote_volume_usdt = 200_000`

* `min_spread_bps_median = 8`

* `min_spread_bps_p25 = 5`

* `maker_fee_bps_assumption = 2` (временно; потом заменим на реальные комиссии аккаунта)

* `slippage_buffer_bps = 2`

* `min_edge_after_fees_bps = 2`

* `shortlist_size = 20`

---

## **11\) Важное замечание (чтобы ожидания были правильные)**

Этот скрипт измеряет **top-of-book спред** и его устойчивость. Он **не** доказывает, что мы “соберём” этот спред на реальных ордерах (там будут очередь, частичные fills, adverse selection). Но он честно отвечает на вопрос:

* “Есть ли вообще **достаточно инструментов**, где спред не схлопнут и объём не мёртвый?”

## **12\) Режим 2: Sanity-check глубины (Order Book Depth) — обязателен для отбора**

### **12.1 Зачем это нужно**

Top-of-book спред (bid/ask) часто “обманчивый”: спред есть, но **объём на лучшей цене микроскопический**, либо стакан тонкий/рваный и любой реальный ордер сразу съедает несколько уровней → спред “на бумаге” превращается в проскальзывание.

Поэтому после первичного отбора по спреду добавляем **depth-проверку**, чтобы ответить:

* “Хватает ли объёма на 1–2 лучших уровнях, чтобы мы могли ставить/снимать маленькие ордера и реально получать maker fills?”

* “Насколько опасен аварийный выход (unwind) на небольшой сумме?”

---

### **12.2 Источник данных**

Используем **REST Order Book**:

* `GET /api/v3/depth?symbol=...&limit=...`

* Weight(IP): **1**

* `limit`: default 100, **max 5000**

* Ответ: `bids: [price, qty]`, `asks: [price, qty]`, `lastUpdateId`

---

### **12.3 Когда и для каких символов делать depth-check**

**Не для всего universe**, чтобы не создавать нагрузку.

Рекомендуемый алгоритм:

1. Сначала выполняем фазу “спред-sampling” (как в ТЗ) и получаем скоринг.

2. Берём кандидатов:

   * `K_depth = top 50` по score (или все `PASS`, если их меньше 50\)

3. Для этих кандидатов запускаем **depth-sampling**:

   * `depth_duration_min` (например 10–30 минут)

   * `depth_interval_sec` (например 30–60 секунд)

   * `depth_limit` (например 20 или 50 уровней; 100 — если нужно оценить unwind в худшем случае)

---

### **12.4 Параметры (добавить в конфиг)**

`depth_check_enabled: true`  
`depth_universe_top_k: 50`  
`depth_duration_min: 20`  
`depth_interval_sec: 30`  
`depth_limit: 20`

`# метрики “достаточной глубины”`  
`min_best_level_notional_usdt: 30        # на лучшем bid и лучшем ask`  
`min_top_n_notional_usdt: 200            # сумма нотионала по top N уровней (N=5/10)`  
`top_n_levels: 5`

`# оценка “псевдо-рыночного выхода” (unwind)`  
`unwind_test_notional_usdt: 50           # например 50 USDT как стресс-тест (1/4 депозита)`  
`max_unwind_slippage_bps: 30             # допустимая оценочная “цена выхода”`  
`band_bps_list: [5, 10, 20]              # глубина в полосе от mid`  
`min_band_notional_usdt:`  
  `"5": 50`  
  `"10": 120`  
  `"20": 250`

---

### **12.5 Что считаем из глубины (формулы)**

Пусть из `/depth` мы получили массивы уровней:

* `bids = [(p1,q1), (p2,q2), ...]` (убывание цены)

* `asks = [(p1,q1), (p2,q2), ...]` (возрастание цены)

Опорные значения:

* `best_bid = bids[0].price`

* `best_ask = asks[0].price`

* `mid = (best_bid + best_ask)/2`

#### **A) Нотионал на лучшем уровне (самое важное)**

* `best_bid_notional = best_bid * bids[0].qty`

* `best_ask_notional = best_ask * asks[0].qty`

Критерий:

* `best_bid_notional >= min_best_level_notional_usdt`

* `best_ask_notional >= min_best_level_notional_usdt`

#### **B) Нотионал на top-N уровнях (устойчивость)**

Для `N = top_n_levels`:

* `topN_bid_notional = Σ_{i=1..N} (bids[i].price * bids[i].qty)`

* `topN_ask_notional = Σ_{i=1..N} (asks[i].price * asks[i].qty)`

Критерий:

* `min(topN_bid_notional, topN_ask_notional) >= min_top_n_notional_usdt`

#### **C) Глубина в полосе ±X bps от mid (приближённая “плотность” около цены)**

Для каждого `band_bps` из `band_bps_list`:

* Полоса для bid: уровни `p >= mid * (1 - band_bps/10000)`

* Полоса для ask: уровни `p <= mid * (1 + band_bps/10000)`

Считаем:

* `band_bid_notional(b) = Σ (p*q) по bids в полосе`

* `band_ask_notional(b) = Σ (p*q) по asks в полосе`

Критерий:

* `min(band_bid_notional(b), band_ask_notional(b)) >= min_band_notional_usdt[b]`

#### **D) Оценка “unwind slippage” для стресс-суммы (псевдо-рыночный выход)**

Это имитация: если нам надо **быстро продать** на `unwind_test_notional_usdt` (в USDT), то мы “проходим” по bids:

Алгоритм расчёта VWAP (для sell):

* нам нужно продать базовый объём `Q_base = unwind_notional / mid`

* идём по bids уровням, на каждом берём `take = min(level_qty, remaining_qty)`

* считаем выручку `revenue += take * level_price`

* `vwap_sell = revenue / Q_base`

* `slippage_bps_sell = (mid - vwap_sell) / mid * 10000`

Аналогично для buy по asks:

* `vwap_buy`

* `slippage_bps_buy = (vwap_buy - mid)/mid * 10000`

Критерий:

* `max(slippage_bps_sell, slippage_bps_buy) <= max_unwind_slippage_bps`

Это не “идеальная симуляция рынка”, но очень полезный sanity-check на тонких стаканах: покажет пары, где аварийный выход на 50 USDT уже даёт огромную потерю.

---

### **12.6 Как использовать результаты depth-check в итоговом PASS/FAIL**

После depth-sampling по символу считаем агрегаты (median/p25) для:

* `best_bid_notional`, `best_ask_notional`

* `topN_bid_notional`, `topN_ask_notional`

* `unwind_slippage_bps_sell/buy` (лучше взять p90 как worst-case)

Добавляем **дополнительный критерий PASS\_DEPTH**:

* `best_level_notional_median >= min_best_level_notional_usdt` (на обе стороны)

* `topN_notional_median >= min_top_n_notional_usdt` (на обе стороны)

* `unwind_slippage_p90 <= max_unwind_slippage_bps`

И финальное решение:

* `PASS_TOTAL = PASS_SPREAD && PASS_DEPTH`

---

### **12.7 Частота и нагрузка (чтобы не словить ограничения)**

`/api/v3/depth` имеет weight 1, limit до 5000  
 Рекомендуемая безопасная схема:

* `K_depth = 50`

* `depth_interval = 30s`  
   → \~1.67 запроса/сек суммарно, что обычно приемлемо для публичного API, плюс у нас остаётся запас под `/ticker/bookTicker` и `/ticker/24hr`.

---

### **12.8 Артефакты вывода (добавить в отчёт)**

В `report.md` добавить:

* сколько из top-50 по score “упало” на depth-check (и почему)

* топ-20 итоговый **после depth**

* таблицу/сводку по каждой shortlisted паре:

  * spread\_median, net\_edge\_bps

  * best\_level\_notional\_median (bid/ask)

  * topN\_notional\_median

  * unwind\_slippage\_p90

