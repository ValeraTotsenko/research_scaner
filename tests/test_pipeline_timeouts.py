from __future__ import annotations

import json
import logging
import time

from scanner.config import AppConfig, PipelineConfig, RawSamplingConfig, SamplingConfig, SpreadSamplingConfig
from scanner.io.layout import create_run_layout
from scanner.pipeline.depth_check import run_depth_check
from scanner.pipeline.runner import EXIT_STAGE_ERROR, PipelineOptions, run_pipeline
from scanner.pipeline.spread_sampling import run_spread_sampling
from scanner.pipeline.stages import StageDefinition


class _NoopClient:
    def get_book_ticker(self) -> None:
        raise AssertionError("client should not be called on timeout")

    def get_book_ticker_symbol(self, _: str) -> None:
        raise AssertionError("client should not be called on timeout")


def test_spread_sampling_timeout(tmp_path) -> None:
    cfg = SamplingConfig(
        spread=SpreadSamplingConfig(duration_s=10, interval_s=1),
        raw=RawSamplingConfig(enabled=False),
    )
    deadline_ts = time.monotonic() - 1
    result = run_spread_sampling(_NoopClient(), ["BTCUSDT"], cfg, tmp_path, deadline_ts=deadline_ts)

    assert result.timed_out is True
    assert result.ticks_success == 0
    assert result.ticks_fail == 0
    assert result.elapsed_s >= 0


class _DepthClient:
    def get_depth(self, *_: object, **__: object) -> None:
        raise AssertionError("depth client should not be called on timeout")


def test_depth_check_timeout(tmp_path) -> None:
    deadline_ts = time.monotonic() - 1
    result = run_depth_check(_DepthClient(), ["BTCUSDT"], AppConfig(), tmp_path, deadline_ts=deadline_ts)

    assert result.timed_out is True
    assert result.depth_requests_total == 0
    assert result.elapsed_s >= 0


def test_runner_records_stage_timeout(tmp_path) -> None:
    layout = create_run_layout(tmp_path, "timeout", AppConfig())
    stage_def = StageDefinition(
        name="universe",
        inputs=(),
        outputs=(),
        run=lambda _: {"timed_out": True},
        validate_inputs=lambda _: [],
        validate_outputs=lambda _: [],
    )
    options = PipelineOptions(
        resume=False,
        force=False,
        fail_fast=True,
        continue_on_error=False,
        dry_run=False,
        artifact_validation="strict",
    )

    exit_code = run_pipeline(
        run_dir=layout.run_dir,
        run_id="timeout",
        config=AppConfig(),
        logger=logging.getLogger("test"),
        metrics_path=layout.metrics_path,
        stage_plan=["universe"],
        options=options,
        stage_definitions=[stage_def],
    )

    assert exit_code == EXIT_STAGE_ERROR

    payload = json.loads((layout.run_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    stage = payload["stages"][0]
    assert stage["status"] == "failed"
    assert stage["error"]["type"] == "StageTimeoutError"


def test_timeout_within_grace_marks_timeout(tmp_path) -> None:
    def _run(ctx) -> dict[str, object]:
        time.sleep(1.1)
        (ctx.run_dir / "raw.jsonl").write_text("ok", encoding="utf-8")
        return {"ticks_success": 6, "ticks_fail": 0}

    cfg = AppConfig(
        sampling=SamplingConfig(
            spread=SpreadSamplingConfig(duration_s=10, interval_s=1, min_uptime=0.5),
            raw=RawSamplingConfig(enabled=False),
        ),
        pipeline=PipelineConfig(
            stage_timeouts_s={"spread": 1},
            timeout_grace_s=2,
            timeout_behavior="fail",
        ),
    )
    layout = create_run_layout(tmp_path, "grace", cfg)
    stage_def = StageDefinition(
        name="spread",
        inputs=(),
        outputs=("raw.jsonl",),
        run=_run,
        validate_inputs=lambda _: [],
        validate_outputs=lambda ctx: [] if (ctx.run_dir / "raw.jsonl").exists() else ["missing"],
    )
    options = PipelineOptions(
        resume=False,
        force=False,
        fail_fast=True,
        continue_on_error=False,
        dry_run=False,
        artifact_validation="strict",
    )

    exit_code = run_pipeline(
        run_dir=layout.run_dir,
        run_id="grace",
        config=cfg,
        logger=logging.getLogger("test"),
        metrics_path=layout.metrics_path,
        stage_plan=["spread"],
        options=options,
        stage_definitions=[stage_def],
    )

    assert exit_code == 0
    payload = json.loads((layout.run_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    assert payload["stages"][0]["status"] == "timeout"


def test_partial_success_timeout_continues(tmp_path) -> None:
    def _spread(ctx) -> dict[str, object]:
        time.sleep(1.1)
        (ctx.run_dir / "raw.jsonl").write_text("ok", encoding="utf-8")
        return {"ticks_success": 6, "ticks_fail": 0}

    def _score(ctx) -> dict[str, object]:
        (ctx.run_dir / "summary.csv").write_text("ok", encoding="utf-8")
        return {}

    cfg = AppConfig(
        sampling=SamplingConfig(
            spread=SpreadSamplingConfig(duration_s=10, interval_s=1, min_uptime=0.5),
            raw=RawSamplingConfig(enabled=False),
        ),
        pipeline=PipelineConfig(
            stage_timeouts_s={"spread": 1},
            timeout_grace_s=2,
            timeout_behavior="partial_success",
        ),
    )
    layout = create_run_layout(tmp_path, "partial", cfg)
    spread_def = StageDefinition(
        name="spread",
        inputs=(),
        outputs=("raw.jsonl",),
        run=_spread,
        validate_inputs=lambda _: [],
        validate_outputs=lambda ctx: [] if (ctx.run_dir / "raw.jsonl").exists() else ["missing"],
    )
    score_def = StageDefinition(
        name="score",
        inputs=("raw.jsonl",),
        outputs=("summary.csv",),
        run=_score,
        validate_inputs=lambda ctx: []
        if (ctx.run_dir / "raw.jsonl").exists()
        else ["missing raw.jsonl"],
        validate_outputs=lambda ctx: [] if (ctx.run_dir / "summary.csv").exists() else ["missing"],
    )
    options = PipelineOptions(
        resume=False,
        force=False,
        fail_fast=True,
        continue_on_error=False,
        dry_run=False,
        artifact_validation="strict",
    )

    exit_code = run_pipeline(
        run_dir=layout.run_dir,
        run_id="partial",
        config=cfg,
        logger=logging.getLogger("test"),
        metrics_path=layout.metrics_path,
        stage_plan=["spread", "score"],
        options=options,
        stage_definitions=[spread_def, score_def],
    )

    payload = json.loads((layout.run_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["stages"][0]["status"] == "timeout"
    assert payload["stages"][1]["status"] == "success"


def test_partial_success_timeout_with_no_data_fails(tmp_path) -> None:
    cfg = AppConfig(
        sampling=SamplingConfig(
            spread=SpreadSamplingConfig(duration_s=10, interval_s=1, min_uptime=0.5),
            raw=RawSamplingConfig(enabled=False),
        ),
        pipeline=PipelineConfig(
            stage_timeouts_s={"spread": 1},
            timeout_grace_s=2,
            timeout_behavior="partial_success",
        ),
    )
    layout = create_run_layout(tmp_path, "nodata", cfg)
    stage_def = StageDefinition(
        name="spread",
        inputs=(),
        outputs=("raw.jsonl",),
        run=lambda _: {"timed_out": True, "ticks_success": 0, "ticks_fail": 0},
        validate_inputs=lambda _: [],
        validate_outputs=lambda ctx: [] if (ctx.run_dir / "raw.jsonl").exists() else ["missing"],
    )
    options = PipelineOptions(
        resume=False,
        force=False,
        fail_fast=True,
        continue_on_error=False,
        dry_run=False,
        artifact_validation="strict",
    )

    exit_code = run_pipeline(
        run_dir=layout.run_dir,
        run_id="nodata",
        config=cfg,
        logger=logging.getLogger("test"),
        metrics_path=layout.metrics_path,
        stage_plan=["spread"],
        options=options,
        stage_definitions=[stage_def],
    )

    payload = json.loads((layout.run_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    assert exit_code == EXIT_STAGE_ERROR
    assert payload["stages"][0]["status"] == "failed"
