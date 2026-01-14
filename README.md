# research_scaner

## Runbook (Ubuntu)

### Prerequisites

- Python 3.11+

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

### Run

```bash
python -m scanner run --config config.yaml --output ./output
```

Dry-run (prints plan + validates artifacts without executing stages):

```bash
python -m scanner run --config config.yaml --output ./output --dry-run
```

Run a subset of stages:

```bash
python -m scanner run --config config.yaml --output ./output --from universe --to score
python -m scanner run --config config.yaml --output ./output --stages universe,spread,score
```

Resume a previous run after a crash:

```bash
python -m scanner run --config config.yaml --output ./output --run-id 20260113_220501Z_ab12cd --resume
```

### Example config (all available settings)

```yaml
mexc:
  base_url: https://api.mexc.com
  timeout_s: 10
  max_retries: 5
  backoff_base_s: 0.5
  backoff_max_s: 8
  max_rps: 2.0
runtime:
  run_name: "example"
  timezone: "UTC"
obs:
  log_jsonl: true
universe:
  quote_asset: "USDT"
  allowed_exchange_status: ["1"]
  min_quote_volume_24h: 100000
  min_trades_24h: 200
  use_quote_volume_estimate: true
  require_trade_count: false
  blacklist_regex: []
  whitelist: []
sampling:
  spread:
    duration_s: 1800
    interval_s: 5
    min_uptime: 0.9
    allow_per_symbol: false
    per_symbol_limit: 50
  depth:
    duration_s: 1200
    interval_s: 30
    limit: 100
  raw:
    enabled: true
    gzip: true
thresholds:
  spread:
    median_max_bps: 25.0
    p90_max_bps: 60.0
  depth:
    best_level_min_notional: 100.0
    unwind_slippage_max_bps: 50.0
  uptime_min: 0.9
fees:
  taker_bps: 4.0
  maker_bps: 2.0
depth:
  top_n_levels: 10
  band_bps: [5, 10, 20]
  stress_notional_usdt: 100.0
report:
  top_n: 20
  include_raw_in_bundle: false
pipeline:
  resume: true
  fail_fast: true
  continue_on_error: false
  artifact_validation: "strict"
  total_timeout_s: 0
  stage_timeouts_s:
    universe: 300
    spread: 3660
    score: 300
    depth: 2460
    report: 300
  timeout_behavior: "fail"
  timeout_grace_s: 2
  safety_margin_s: 5
  spread_timeout_behavior: "warn"
```

### Deliverables for traders

Each run creates a folder like `output/run_<id>` that can be handed to a trader. Generate the final report pack with:

```bash
python - <<'PY'
from pathlib import Path

from scanner.config import load_config
from scanner.io.bundle import create_run_bundle
from scanner.report.report_md import generate_report

cfg = load_config(Path("config.yaml")).config
run_dir = Path("output/run_<id>")

generate_report(run_dir, cfg)
create_run_bundle(run_dir, cfg)
PY
```

The run folder will contain:

- `report.md` (human-readable summary)
- `shortlist.csv` (top candidates)
- `run_bundle.zip` (portable bundle with summary/depth/meta/config/report)
- `pipeline_state.json` (stage execution state and errors)
