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
python -m scanner --config config.yaml --output ./output
```

Dry-run (validates config and creates run layout only):

```bash
python -m scanner --config config.yaml --output ./output --dry-run
```

### Example config

```yaml
mexc:
  base_url: https://api.mexc.com
runtime:
  run_name: "example"
  timezone: "UTC"
obs:
  log_jsonl: true
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
