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
