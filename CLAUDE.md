# CLAUDE.md - AI Assistant Guide for research_scaner

**Version:** 0.1.0
**Last Updated:** 2026-01-16
**Project:** MEXC Spread Feasibility Research Scanner

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Codebase Structure](#codebase-structure)
3. [Architecture & Design Patterns](#architecture--design-patterns)
4. [Development Workflows](#development-workflows)
5. [Key Conventions](#key-conventions)
6. [Testing Guidelines](#testing-guidelines)
7. [Common Tasks & Recipes](#common-tasks--recipes)
8. [Important Files & Locations](#important-files--locations)
9. [Git & Deployment](#git--deployment)
10. [AI Assistant Guidelines](#ai-assistant-guidelines)

---

## Project Overview

### Purpose

**research_scaner** is a production-grade cryptocurrency market research tool that analyzes MEXC exchange trading pairs to identify profitable arbitrage opportunities. It evaluates:

- **Bid-ask spreads** across time to determine market-making viability
- **Order book depth** for slippage and liquidity assessment
- **24-hour volume and trades** for universe filtering
- **Net edge calculations** accounting for maker/taker fees

### Key Characteristics

- **Language:** Python 3.10.12+ (uses modern type hints, dataclasses)
- **Dependencies:** pydantic (config), PyYAML (config files), httpx (HTTP client), pytest (testing)
- **Architecture:** 5-stage pipeline with resumability and state persistence
- **Output:** CSV/JSON data artifacts, markdown reports, and portable ZIP bundles
- **Observability:** Structured JSON Lines logging, HTTP metrics tracking, API health monitoring

### Business Context

The tool is used by traders to:
1. Discover high-quality trading pairs for market-making strategies
2. Generate research reports with quantitative risk metrics
3. Filter symbols by spread corridors (min/max thresholds)
4. Assess depth liquidity for position sizing

Each run produces a `run_bundle.zip` that can be handed off to traders.

---

## Codebase Structure

```
/home/user/research_scaner/
├── scanner/                      # Main application package (v0.1.0)
│   ├── __init__.py              # Package version
│   ├── __main__.py              # CLI entry point (run/cleanup subcommands)
│   ├── config.py                # Pydantic config validation (10+ sections)
│   ├── cleanup.py               # Artifact cleanup utility
│   │
│   ├── analytics/               # Statistical analysis
│   │   ├── scoring.py          # Edge calculations, pass/fail logic
│   │   ├── spread_stats.py     # Percentile computation (p10/p25/p90)
│   │   └── depth_metrics.py    # Order book analysis, slippage
│   │
│   ├── io/                      # Input/output modules
│   │   ├── layout.py           # Run directory creation, resumability
│   │   ├── export_universe.py  # universe.json + rejects CSV
│   │   ├── summary_export.py   # summary.csv/json with 20+ columns
│   │   ├── depth_export.py     # depth_metrics.csv + enriched summary
│   │   ├── raw_writer.py       # JSONL writer (optional gzip)
│   │   └── bundle.py           # ZIP artifact creation
│   │
│   ├── mexc/                    # MEXC API client
│   │   ├── client.py           # HTTP client with rate limiting, retries
│   │   ├── errors.py           # Error classification (429, 403, 5xx)
│   │   └── ticker_24h.py       # 24-hour stats parsing
│   │
│   ├── models/                  # Data models
│   │   ├── universe.py         # UniverseResult (symbols, rejects)
│   │   ├── spread.py           # SpreadSampleResult (ticks, uptime)
│   │   └── depth.py            # DepthCheckResult (pass/fail with reasons)
│   │
│   ├── obs/                     # Observability
│   │   ├── logging.py          # JSON Lines structured logging
│   │   └── metrics.py          # HTTP metrics, latency histograms
│   │
│   ├── pipeline/                # Pipeline orchestration
│   │   ├── runner.py           # Main executor (timeout, resume, state)
│   │   ├── stages.py           # 5 stage definitions (universe → report)
│   │   ├── state.py            # PipelineState tracking (spec v0.1)
│   │   ├── errors.py           # Pipeline-specific errors
│   │   ├── universe.py         # Universe stage (filtering)
│   │   ├── spread_sampling.py  # Spread stage (data collection)
│   │   ├── ticker_24h.py       # 24h stats enrichment
│   │   └── depth_check.py      # Depth stage (slippage analysis)
│   │
│   ├── report/                  # Report generation
│   │   └── report_md.py        # Markdown report + shortlist CSV
│   │
│   └── validation/              # Artifact validation
│       └── artifacts.py        # CSV/JSON schema validation
│
├── tests/                       # 19 test files
│   ├── fixtures/               # Test data (universe.json)
│   ├── test_config.py          # Config validation
│   ├── test_universe.py        # Symbol filtering
│   ├── test_spread_*.py        # Spread sampling/stats
│   ├── test_scoring*.py        # Edge calculations
│   ├── test_depth_*.py         # Depth metrics/checks
│   ├── test_pipeline_*.py      # Pipeline execution
│   ├── test_mexc_client.py     # HTTP client
│   └── test_*.py               # Other unit tests
│
├── configs/                     # Pre-configured settings
│   ├── strict.yaml             # Production research config
│   └── smoke.yaml              # Fast validation config
│
├── deploy/                      # Deployment scripts
│   ├── systemd/                # Systemd unit files
│   └── (systemd service files)
│
├── scripts/                     # Automation scripts
│   ├── install_service.sh      # Systemd installation
│   ├── run_service.sh          # Service wrapper
│   ├── install_logrotate.sh    # Log rotation setup
│   └── uninstall_service.sh    # Cleanup
│
├── docs/                        # Documentation
│   ├── spec.md                 # Scoring/filtering specification
│   └── howto/                  # Operational guides
│       └── run_as_service.md
│
├── pyproject.toml              # Python project metadata
├── config.yaml                 # Default configuration
├── README.md                   # Quick start guide
├── CODEX_TaskBriefs_Feedback_Fixes.md  # Recent fix pack
└── .gitignore
```

---

## Architecture & Design Patterns

### 1. Five-Stage Pipeline

The core workflow is a directed acyclic graph (DAG) with strict dependencies:

```
universe → spread → score → depth → report
```

| Stage | Inputs | Outputs | Purpose | Typical Duration |
|-------|--------|---------|---------|------------------|
| **universe** | None | `universe.json` | Fetch all MEXC pairs, filter by quote asset, volume, trades | 30-180s |
| **spread** | `universe.json` | `raw_bookticker.jsonl[.gz]` | Sample bid-ask spreads at intervals (e.g., 5s for 10-30min) | 600-3600s |
| **score** | universe + spread raw | `summary.csv`, `summary.json` | Compute spread stats, edge metrics, pass/fail | 30-180s |
| **depth** | `summary.csv` | `depth_metrics.csv`, `summary_enriched.csv` | Analyze order book, evaluate slippage | 600-2400s |
| **report** | summary + meta | `report.md`, `shortlist.csv` | Generate human-readable report | 30-180s |

**Key Files:**
- `/home/user/research_scaner/scanner/pipeline/runner.py:1` - Pipeline executor
- `/home/user/research_scaner/scanner/pipeline/stages.py:31` - `STAGE_ORDER` definition
- `/home/user/research_scaner/scanner/pipeline/stages.py:34` - `StageContext` and `StageDefinition`

### 2. State Persistence & Resumability

- **Pipeline State:** `pipeline_state.json` tracks completion status, metrics, errors per stage
- **Spec Version:** v0.1 (checked on resume for compatibility)
- **Resume Logic:** Skip completed stages, re-run failed/missing stages
- **Force Flag:** `--force` re-runs completed stages

**Implementation:**
- `/home/user/research_scaner/scanner/pipeline/state.py:1` - State management
- `/home/user/research_scaner/scanner/io/layout.py:1` - Directory initialization

### 3. Configuration System

Pydantic-based hierarchical config with 10+ sections:

```yaml
mexc:           # API client settings
runtime:        # Run metadata (name, timezone)
obs:            # Logging settings
universe:       # Symbol filtering rules
sampling:       # Spread/depth sampling parameters
thresholds:     # Pass/fail criteria
fees:           # Maker/taker fees
depth:          # Depth analysis settings
report:         # Report generation options
pipeline:       # Execution control (timeouts, resume, validation)
```

**Key Principles:**
- All thresholds externalized (no hardcoded business logic)
- Multiple profiles supported (`configs/strict.yaml`, `configs/smoke.yaml`)
- Validation at load time (prevents invalid runs)

**Implementation:**
- `/home/user/research_scaner/scanner/config.py:1` - Full config schema

### 4. Error Classification & Resilience

**Error Types** (from `/home/user/research_scaner/scanner/mexc/errors.py:1`):
- `RateLimitedError` (HTTP 429) → Retry with exponential backoff
- `WafLimitedError` (HTTP 403) → Log as API degradation
- `TransientHttpError` (5xx, timeouts) → Retry
- `FatalHttpError` (4xx except 429/403) → Fail immediately

**Resilience Mechanisms:**
1. **Rate Limiting:** Token bucket (configurable RPS via `max_rps`)
2. **Retries:** Exponential backoff (base: 0.5s, max: 8s, up to 5 retries)
3. **Timeouts:** Per-stage deadlines with grace periods
4. **Graceful Degradation:** Spread stage allows partial success if `min_uptime` met

### 5. Observability-First Design

**Logging:**
- JSON Lines format (`logs.jsonl`) for machine parsing
- Fields: timestamp, level, module, event, run_id, extra
- Per-run loggers (isolated by run directory)

**Metrics:**
- `metrics.json` tracks HTTP request counts, latencies, errors, retries
- API health summarization (run marked degraded if 403/429/5xx detected)
- Latency histogram buckets: 25ms, 50ms, 100ms, 200ms, 500ms, 1s, 2s, 5s

**Implementation:**
- `/home/user/research_scaner/scanner/obs/logging.py:1` - JsonLineFormatter
- `/home/user/research_scaner/scanner/obs/metrics.py:1` - Metrics tracking

### 6. Data Lineage & Traceability

Every run creates a versioned directory:
```
output/run_<YYYYMMDD>_<HHMMSSZ>_<6-hex>/
```

**Artifact Manifest:**
- `run_meta.json` - Config snapshot, git commit SHA, status, health
- `pipeline_state.json` - Per-stage execution records
- `metrics.json` - API performance data
- `logs.jsonl` - Event log
- All data artifacts (universe, raw, summary, depth, report)

---

## Development Workflows

### Setup

```bash
# Prerequisites: Python 3.11+ (tested on 3.10.12+)
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .          # Editable install
pip install -e .[dev]     # With pytest
```

### Running the Scanner

**Full Pipeline:**
```bash
python -m scanner run --config config.yaml --output ./output
```

**Dry Run (validation only):**
```bash
python -m scanner run --config config.yaml --output ./output --dry-run
```

**Subset of Stages:**
```bash
# Run from universe to score (skip depth, report)
python -m scanner run --config config.yaml --output ./output --from universe --to score

# Run specific stages
python -m scanner run --config config.yaml --output ./output --stages universe,spread,score
```

**Resume After Crash:**
```bash
python -m scanner run --config config.yaml --output ./output --run-id 20260113_220501Z_ab12cd --resume
```

**Force Re-run Completed Stages:**
```bash
python -m scanner run --config config.yaml --output ./output --run-id <id> --resume --force
```

### Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_scoring.py

# Run with verbose output
pytest -v

# Run tests matching pattern
pytest -k "spread"
```

**Test Structure:**
- Unit tests use simple dataclasses/dicts (no mocking frameworks)
- Fixtures in `tests/fixtures/` (e.g., `universe.json`)
- Test naming: `test_<module>_<function>_<scenario>`

### Cleanup Old Runs

```bash
# Keep last 20 runs or runs from last 7 days
python -m scanner cleanup --output ./output --keep-days 7 --keep-last 20

# Dry run (preview deletions)
python -m scanner cleanup --output ./output --keep-days 7 --keep-last 20 --dry-run
```

### Generating Reports Manually

```python
from pathlib import Path
from scanner.config import load_config
from scanner.io.bundle import create_run_bundle
from scanner.report.report_md import generate_report

cfg = load_config(Path("config.yaml")).config
run_dir = Path("output/run_<id>")

generate_report(run_dir, cfg)
create_run_bundle(run_dir, cfg)
```

---

## Key Conventions

### Code Style

1. **Type Hints:** Always use type hints (PEP 484/585 style)
   ```python
   def compute_spread_stats(samples: list[SpreadSample]) -> SpreadStats:
   ```

2. **Dataclasses:** Use `@dataclass(frozen=True)` for immutable models
   ```python
   @dataclass(frozen=True)
   class SpreadStats:
       symbol: str
       spread_median_bps: float
   ```

3. **Pydantic Models:** Use for config validation (auto-validation, JSON schema)
   ```python
   class SpreadThresholdsConfig(BaseModel):
       median_min_bps: float = Field(default=8.0, ge=0)
   ```

4. **Naming Conventions:**
   - **Modules:** `snake_case.py`
   - **Classes:** `PascalCase`
   - **Functions/variables:** `snake_case`
   - **Constants:** `UPPER_SNAKE_CASE`

5. **Private Functions:** Prefix with `_` for internal helpers
   ```python
   def _parse_float(value: object) -> float | None:
   ```

### Error Handling

1. **HTTP Errors:** Always classify using `scanner.mexc.errors` types
2. **Retries:** Use exponential backoff from `MexcClient`
3. **Stage Failures:** Return `None` or raise exception (logged in pipeline state)
4. **Validation:** Use `ValidationResult` from `scanner.validation.artifacts`

### Logging

1. **Use `log_event()` for structured logs:**
   ```python
   from scanner.obs.logging import log_event
   log_event(logger, "spread_sampling_started", symbol_count=len(symbols))
   ```

2. **Log Levels:**
   - `DEBUG`: Detailed diagnostic info
   - `INFO`: Key milestones (stage start/end, counts)
   - `WARNING`: Degraded state (rate limits, partial failures)
   - `ERROR`: Stage failures, unrecoverable errors

3. **Avoid print()**: Always use logger

### Configuration

1. **Never hardcode thresholds** - externalize to config.yaml
2. **Use Pydantic Field() for validation:**
   ```python
   min_quote_volume_24h: float = Field(default=100_000, ge=0)
   ```
3. **Provide sensible defaults** in model definitions
4. **Document units** in field names (e.g., `_bps`, `_s`, `_usdt`)

### Data Artifacts

1. **JSON:** Use UTF-8 encoding, pretty-print with indent=2
2. **CSV:** Include headers, use `,` delimiter, quote all text fields
3. **JSONL:** One JSON object per line (for streaming/large datasets)
4. **Gzip:** Optional compression for raw data (controlled by `sampling.raw.gzip`)

### File Naming

- **Artifacts:** `snake_case.{json,csv,md,jsonl}`
- **Run Directories:** `run_<YYYYMMDD>_<HHMMSSZ>_<6-hex>/`
- **Bundles:** `run_bundle.zip`

---

## Testing Guidelines

### Test Organization

```
tests/
├── fixtures/           # Test data
│   └── universe.json
├── test_<module>.py    # Unit tests for scanner/<module>.py
```

### Test Naming

```python
def test_<function>_<scenario>() -> None:
    # Example: test_score_symbol_pass_spread()
```

### Test Structure

```python
def test_compute_spread_stats_basic() -> None:
    # Arrange
    samples = [
        SpreadSample(symbol="BTCUSDT", bid=50000.0, ask=50010.0, ts_ms=0),
        SpreadSample(symbol="BTCUSDT", bid=50005.0, ask=50015.0, ts_ms=1000),
    ]

    # Act
    result = compute_spread_stats(samples, quote_volume_24h=1_000_000, trades_24h=500)

    # Assert
    assert result.spread_median_bps == pytest.approx(20.0, abs=0.1)
    assert result.uptime == 1.0
```

### Coverage Goals

- **Unit tests:** Cover all analytics functions (scoring, stats, metrics)
- **Integration tests:** Cover pipeline stages, state management
- **Fixtures:** Minimal realistic data (e.g., 2-5 symbols)

### Running Tests

```bash
# All tests
pytest

# Specific module
pytest tests/test_scoring.py

# Verbose
pytest -v

# Stop on first failure
pytest -x

# Show print statements
pytest -s
```

---

## Common Tasks & Recipes

### Task 1: Add a New Threshold

1. **Update config schema** in `scanner/config.py`:
   ```python
   class ThresholdsConfig(BaseModel):
       new_threshold_bps: float = Field(default=10.0, ge=0)
   ```

2. **Update scoring logic** in `scanner/analytics/scoring.py`:
   ```python
   if spread_stats.some_metric > cfg.thresholds.new_threshold_bps:
       fail_reasons.append("new_threshold_exceeded")
   ```

3. **Update tests** in `tests/test_scoring.py`:
   ```python
   def test_score_symbol_new_threshold() -> None:
       # Test pass/fail with new threshold
   ```

4. **Update spec** in `docs/spec.md` (document the threshold)

5. **Update config.yaml** with sensible default

### Task 2: Add a New Fail Reason

1. **Add to scoring logic** in `scanner/analytics/scoring.py`:
   ```python
   if condition:
       fail_reasons.append("new_fail_reason")
   ```

2. **Update ScoreResult** to include in tuple type

3. **Add test** in `tests/test_scoring.py`:
   ```python
   def test_score_symbol_flags_new_fail_reason() -> None:
       # Test that new fail reason appears
   ```

4. **Document** in `docs/spec.md` under "Fail reasons"

### Task 3: Modify a Pipeline Stage

1. **Update stage function** in `scanner/pipeline/<stage>.py`
2. **Update validation** in `scanner/pipeline/stages.py` (inputs/outputs)
3. **Update tests** in `tests/test_pipeline_*.py`
4. **Consider state migration** if artifacts change (bump spec version?)

### Task 4: Add New API Endpoint

1. **Add method** to `scanner/mexc/client.py`:
   ```python
   def get_new_endpoint(self, symbol: str) -> dict[str, object]:
       return self._get(f"/api/v3/new_endpoint?symbol={symbol}")
   ```

2. **Add error handling** (use existing retry/backoff)

3. **Add test** in `tests/test_mexc_client.py`

4. **Update metrics** if new endpoint category

### Task 5: Debug a Failed Run

1. **Check pipeline state:**
   ```bash
   cat output/run_<id>/pipeline_state.json | jq
   ```

2. **Review logs:**
   ```bash
   cat output/run_<id>/logs.jsonl | jq -r '.event'
   ```

3. **Check metrics for API issues:**
   ```bash
   cat output/run_<id>/metrics.json | jq .http
   ```

4. **Resume with verbose logging:**
   ```bash
   python -m scanner run --config config.yaml --output ./output --run-id <id> --resume --log-level DEBUG
   ```

### Task 6: Create a New Config Profile

1. Copy existing profile:
   ```bash
   cp configs/strict.yaml configs/myprofile.yaml
   ```

2. Modify thresholds/sampling parameters

3. Run:
   ```bash
   python -m scanner run --config configs/myprofile.yaml --output ./output
   ```

---

## Important Files & Locations

### Core Entry Points

| File | Purpose | Key Functions/Classes |
|------|---------|----------------------|
| `scanner/__main__.py` | CLI entry point | `main()`, arg parsing, run ID generation |
| `scanner/pipeline/runner.py` | Pipeline executor | `run_pipeline()`, timeout enforcement |
| `scanner/pipeline/stages.py` | Stage definitions | `STAGE_ORDER`, `StageDefinition`, stage functions |

### Configuration

| File | Purpose |
|------|---------|
| `scanner/config.py` | Pydantic config schema (all sections) |
| `config.yaml` | Default configuration |
| `configs/strict.yaml` | Production research config |
| `configs/smoke.yaml` | Fast validation config |

### Business Logic

| File | Purpose | Key Functions |
|------|---------|---------------|
| `scanner/analytics/scoring.py` | Edge calculations, pass/fail | `score_symbol()`, `ScoreResult` |
| `scanner/analytics/spread_stats.py` | Percentile computation | `compute_spread_stats()`, `SpreadStats` |
| `scanner/analytics/depth_metrics.py` | Order book analysis | `compute_depth_snapshot_metrics()` |

### Data I/O

| File | Purpose |
|------|---------|
| `scanner/io/layout.py` | Run directory creation, resumability |
| `scanner/io/summary_export.py` | Export summary.csv/json (20+ columns) |
| `scanner/io/depth_export.py` | Export depth_metrics.csv + enriched summary |
| `scanner/io/bundle.py` | Create run_bundle.zip |

### MEXC Integration

| File | Purpose |
|------|---------|
| `scanner/mexc/client.py` | HTTP client, rate limiting, retries |
| `scanner/mexc/errors.py` | Error classification |

### Validation

| File | Purpose |
|------|---------|
| `scanner/validation/artifacts.py` | CSV/JSON schema validation |

### Reports

| File | Purpose |
|------|---------|
| `scanner/report/report_md.py` | Generate report.md + shortlist.csv |

### Documentation

| File | Purpose |
|------|---------|
| `README.md` | Quick start guide |
| `docs/spec.md` | Scoring/filtering specification (CRITICAL) |
| `docs/howto/run_as_service.md` | Systemd deployment guide |
| `CODEX_TaskBriefs_Feedback_Fixes.md` | Recent task briefs and fixes |

---

## Git & Deployment

### Branch Strategy

- **Main branch:** Production-ready code (usually `main` or `master`)
- **Feature branches:** `codex/<feature-name>` for task-specific work
- **AI branches:** `claude/claude-md-<session-id>` for AI assistant work

### Commit Conventions

- Use descriptive messages (imperative mood)
- Reference PR numbers in merge commits
- Examples from history:
  - `Fix missing 24h stats and effective volume`
  - `Add explicit depth criteria outputs and reasons`
  - `Standardize API error health reporting`

### Recent Changes (from git log)

```
a9e4e4e - Standardize API error health reporting (#37)
e402ed4 - Add explicit depth criteria outputs and reasons (#36)
512f732 - Add edge metrics and shortlist threshold (#35)
f862819 - Add spread min/max thresholds (#34)
58be3bd - Fix universe build with defaultSymbols base (#33)
cc30329 - Fix missing 24h stats and effective volume (#32)
```

### Deployment

**Systemd Service:**
```bash
# Install service
sudo ./scripts/install_service.sh

# Run manually
./scripts/run_service.sh

# Install log rotation
sudo ./scripts/install_logrotate.sh

# Uninstall
sudo ./scripts/uninstall_service.sh
```

**Service Files:**
- `deploy/systemd/*.service` - Unit files
- See `docs/howto/run_as_service.md` for details

---

## AI Assistant Guidelines

### When Modifying Code

1. **Read Before Writing:**
   - ALWAYS read the file before editing
   - Understand existing patterns and conventions
   - Check for similar implementations elsewhere

2. **Maintain Consistency:**
   - Follow existing type hint style
   - Use same error handling patterns
   - Match logging approach (use `log_event()`)

3. **Preserve Business Logic:**
   - Never change thresholds without explicit request
   - Don't modify scoring formulas without specification update
   - Keep fail reasons explicit and documented

4. **Update Tests:**
   - Add tests for new functionality
   - Update tests when changing behavior
   - Ensure existing tests still pass

5. **Update Documentation:**
   - Update `docs/spec.md` for business logic changes
   - Update this CLAUDE.md for structural changes
   - Update docstrings for public APIs

### When Debugging

1. **Check Pipeline State First:**
   ```bash
   cat output/run_<id>/pipeline_state.json | jq
   ```

2. **Review Structured Logs:**
   ```bash
   cat output/run_<id>/logs.jsonl | jq -r 'select(.level=="ERROR")'
   ```

3. **Analyze Metrics for API Issues:**
   ```bash
   cat output/run_<id>/metrics.json | jq '.http.status_codes'
   ```

4. **Use Dry Run for Config Validation:**
   ```bash
   python -m scanner run --config config.yaml --output ./output --dry-run
   ```

### Critical Areas (Handle with Care)

1. **Scoring Logic** (`scanner/analytics/scoring.py`):
   - Edge calculations must match `docs/spec.md`
   - Fail reasons must be explicit
   - Changes require specification updates

2. **Pipeline State** (`scanner/pipeline/state.py`):
   - Spec version changes require migration logic
   - Breaking changes affect resumability

3. **Configuration Schema** (`scanner/config.py`):
   - Breaking changes require profile updates
   - Validation constraints must be sensible

4. **MEXC Client** (`scanner/mexc/client.py`):
   - Rate limiting is critical (avoid 429s)
   - Retry logic must handle transient errors
   - Metrics tracking must be accurate

### Common Pitfalls to Avoid

1. **Don't hardcode thresholds** - use config
2. **Don't use print()** - use structured logging
3. **Don't skip validation** - always validate artifacts
4. **Don't ignore error classification** - use proper error types
5. **Don't modify artifacts without schema updates** - bump spec version if needed
6. **Don't use mocking in tests** - use simple data structures
7. **Don't change scoring without spec update** - business logic must be documented

### Preferred Patterns

```python
# Good: Structured logging
from scanner.obs.logging import log_event
log_event(logger, "stage_started", stage="spread", symbol_count=len(symbols))

# Good: Pydantic validation
class Config(BaseModel):
    threshold: float = Field(ge=0, le=100)

# Good: Dataclass for immutable data
@dataclass(frozen=True)
class Result:
    value: float
    status: str

# Good: Explicit error handling
try:
    data = client.get_data(symbol)
except RateLimitedError:
    log_event(logger, "rate_limited", symbol=symbol)
    raise

# Bad: Print statements
print("Starting stage...")  # Don't do this

# Bad: Hardcoded threshold
if spread > 25.0:  # Don't do this - use config.thresholds.spread.max_bps

# Bad: Mutable default arguments
def process(items=[]):  # Don't do this - use None and create list inside
```

### Working with CODEX_TaskBriefs_Feedback_Fixes.md

This file contains:
- **Decision Log** (AD-101 to AD-108): Architectural decisions from trader feedback
- **Task Briefs**: Structured fix specifications

When implementing features:
1. Check if a relevant task brief exists
2. Follow the decision log for context
3. Ensure implementation matches the "Scope" section
4. Update artifact schemas as specified in "Требования к интерфейсам"

### Understanding the Specs

**`docs/spec.md`** is the single source of truth for:
- Spread formula: `spread_bps = (ask - bid) / mid * 10_000`
- Percentile calculation (linear interpolation)
- Net edge formula: `spread_median_bps - (maker_bps + taker_bps)`
- Pass/fail criteria
- Score formula

**Always consult spec.md before modifying business logic.**

---

## Quick Reference

### Run Commands

```bash
# Full run
python -m scanner run --config config.yaml --output ./output

# Dry run
python -m scanner run --config config.yaml --output ./output --dry-run

# Resume
python -m scanner run --config config.yaml --output ./output --run-id <id> --resume

# Subset
python -m scanner run --config config.yaml --output ./output --from spread --to depth

# Cleanup
python -m scanner cleanup --output ./output --keep-days 7 --keep-last 20
```

### Key File Paths

```python
# Run directory
run_dir = Path("output/run_<YYYYMMDD>_<HHMMSSZ>_<6-hex>")

# Artifacts
universe_path = run_dir / "universe.json"
raw_path = run_dir / "raw_bookticker.jsonl.gz"
summary_path = run_dir / "summary.csv"
depth_path = run_dir / "depth_metrics.csv"
report_path = run_dir / "report.md"
bundle_path = run_dir / "run_bundle.zip"

# State
state_path = run_dir / "pipeline_state.json"
meta_path = run_dir / "run_meta.json"
logs_path = run_dir / "logs.jsonl"
metrics_path = run_dir / "metrics.json"
```

### Environment

```bash
# Python version
python --version  # Should be 3.10.12+

# Install
pip install -e .          # Base
pip install -e .[dev]     # With pytest

# Tests
pytest                    # All
pytest -v                 # Verbose
pytest -k "spread"        # Pattern match
pytest tests/test_scoring.py  # Single file
```

---

## Version History

- **v0.1.0** (2026-01-16): Initial CLAUDE.md creation
  - Comprehensive codebase analysis
  - Full architecture documentation
  - Development workflows and conventions
  - AI assistant guidelines

---

## Contact & Support

- **Git Repository:** Check remote origin for repo URL
- **Issues:** See `CODEX_TaskBriefs_Feedback_Fixes.md` for recent task briefs
- **Specs:** Refer to `docs/spec.md` for business logic specification

---

**End of CLAUDE.md**
