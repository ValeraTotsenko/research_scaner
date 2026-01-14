import json
import logging
from pathlib import Path

import pytest

from scanner.config import AppConfig
from scanner.pipeline.runner import (
    EXIT_STAGE_ERROR,
    EXIT_VALIDATION_ERROR,
    PipelineOptions,
    build_stage_plan,
    run_pipeline,
)
from scanner.pipeline.stages import StageContext, StageDefinition


def _logger() -> logging.Logger:
    logger = logging.getLogger("scanner.pipeline.test")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def _write_text(path: Path, content: str = "ok") -> None:
    path.write_text(content, encoding="utf-8")


def _make_stage(
    name: str,
    *,
    inputs: tuple[str, ...] = (),
    outputs: tuple[str, ...] = (),
    run_fn=None,
) -> StageDefinition:
    def _run(ctx: StageContext) -> dict[str, object]:
        if run_fn:
            return run_fn(ctx)
        for output in outputs:
            _write_text(ctx.run_dir / output)
        return {}

    def _validate_inputs(ctx: StageContext) -> list[str]:
        errors = []
        for input_name in inputs:
            if not (ctx.run_dir / input_name).exists():
                errors.append(f"Missing {input_name}")
        return errors

    def _validate_outputs(ctx: StageContext) -> list[str]:
        errors = []
        for output_name in outputs:
            if not (ctx.run_dir / output_name).exists():
                errors.append(f"Missing {output_name}")
        return errors

    return StageDefinition(
        name=name,
        inputs=inputs,
        outputs=outputs,
        run=_run,
        validate_inputs=_validate_inputs,
        validate_outputs=_validate_outputs,
    )


def _default_options() -> PipelineOptions:
    return PipelineOptions(
        resume=True,
        force=False,
        fail_fast=True,
        continue_on_error=False,
        dry_run=False,
        artifact_validation="strict",
    )


def test_stage_plan_from_to() -> None:
    plan = build_stage_plan(selected_stages=None, stage_from="spread", stage_to="depth")
    assert plan == ["spread", "score", "depth"]


def test_resume_skips_when_outputs_valid(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_1"
    run_dir.mkdir()
    _write_text(run_dir / "alpha.txt")

    stages = [_make_stage("alpha", outputs=("alpha.txt",), run_fn=lambda _: pytest.fail("should skip"))]
    config = AppConfig()
    exit_code = run_pipeline(
        run_dir=run_dir,
        run_id="run_1",
        config=config,
        logger=_logger(),
        metrics_path=run_dir / "metrics.json",
        stage_plan=["alpha"],
        options=_default_options(),
        stage_definitions=stages,
    )

    assert exit_code == 0
    state = json.loads((run_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    assert state["stages"][0]["status"] == "skipped"


def test_artifact_validation_rejects_broken_summary(tmp_path: Path) -> None:
    from scanner.validation.artifacts import validate_summary_csv

    path = tmp_path / "summary.csv"
    path.write_text("symbol,score\nAAA,1\n", encoding="utf-8")

    result = validate_summary_csv(path, strict=True)
    assert result.valid is False


def test_pipeline_runs_stages_in_order(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_1"
    run_dir.mkdir()
    stages = [
        _make_stage("alpha", outputs=("alpha.txt",)),
        _make_stage("beta", inputs=("alpha.txt",), outputs=("beta.txt",)),
        _make_stage("gamma", inputs=("beta.txt",), outputs=("gamma.txt",)),
    ]
    exit_code = run_pipeline(
        run_dir=run_dir,
        run_id="run_1",
        config=AppConfig(),
        logger=_logger(),
        metrics_path=run_dir / "metrics.json",
        stage_plan=["alpha", "beta", "gamma"],
        options=_default_options(),
        stage_definitions=stages,
    )

    assert exit_code == 0
    assert (run_dir / "gamma.txt").exists()


def test_stage_failure_returns_exit_code(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_1"
    run_dir.mkdir()

    def _fail(_: StageContext) -> dict[str, object]:
        raise RuntimeError("boom")

    stages = [
        _make_stage("alpha", outputs=("alpha.txt",)),
        _make_stage("beta", inputs=("alpha.txt",), outputs=("beta.txt",), run_fn=_fail),
    ]
    exit_code = run_pipeline(
        run_dir=run_dir,
        run_id="run_1",
        config=AppConfig(),
        logger=_logger(),
        metrics_path=run_dir / "metrics.json",
        stage_plan=["alpha", "beta"],
        options=_default_options(),
        stage_definitions=stages,
    )

    assert exit_code == EXIT_STAGE_ERROR


def test_missing_prereq_returns_validation_error(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_1"
    run_dir.mkdir()
    stages = [_make_stage("beta", inputs=("alpha.txt",), outputs=("beta.txt",))]
    exit_code = run_pipeline(
        run_dir=run_dir,
        run_id="run_1",
        config=AppConfig(),
        logger=_logger(),
        metrics_path=run_dir / "metrics.json",
        stage_plan=["beta"],
        options=_default_options(),
        stage_definitions=stages,
    )

    assert exit_code == EXIT_VALIDATION_ERROR


def test_continue_on_error_runs_remaining_stages(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_1"
    run_dir.mkdir()

    def _fail(_: StageContext) -> dict[str, object]:
        raise RuntimeError("boom")

    stages = [
        _make_stage("alpha", outputs=("alpha.txt",)),
        _make_stage("beta", outputs=("beta.txt",), run_fn=_fail),
        _make_stage("gamma", outputs=("gamma.txt",)),
    ]
    options = _default_options()
    options = PipelineOptions(
        resume=options.resume,
        force=options.force,
        fail_fast=False,
        continue_on_error=True,
        dry_run=options.dry_run,
        artifact_validation=options.artifact_validation,
    )
    exit_code = run_pipeline(
        run_dir=run_dir,
        run_id="run_1",
        config=AppConfig(),
        logger=_logger(),
        metrics_path=run_dir / "metrics.json",
        stage_plan=["alpha", "beta", "gamma"],
        options=options,
        stage_definitions=stages,
    )

    assert exit_code == EXIT_STAGE_ERROR
    assert (run_dir / "gamma.txt").exists()
