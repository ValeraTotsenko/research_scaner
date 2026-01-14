from __future__ import annotations

import json
import logging
import time

from scanner.config import AppConfig, RawSamplingConfig, SamplingConfig, SpreadSamplingConfig
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
