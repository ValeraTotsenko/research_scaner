from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from scanner import __version__
from scanner.config import AppConfig
from scanner.mexc.client import MexcClient
from scanner.obs.logging import log_event
from scanner.obs.metrics import update_metrics
from scanner.pipeline.stages import (
    STAGE_ORDER,
    StageContext,
    StageDefinition,
    default_stage_definitions,
    ensure_stage_order,
    validate_stage_names,
)
from scanner.pipeline.state import (
    PIPELINE_SPEC_VERSION,
    SpecVersionMismatchError,
    create_pipeline_state,
    load_pipeline_state,
    write_pipeline_state,
)


EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_STAGE_ERROR = 3
EXIT_VALIDATION_ERROR = 4


@dataclass(frozen=True)
class PipelineOptions:
    resume: bool
    force: bool
    fail_fast: bool
    continue_on_error: bool
    dry_run: bool
    artifact_validation: str


def build_stage_plan(
    *,
    selected_stages: Sequence[str] | None,
    stage_from: str | None,
    stage_to: str | None,
) -> list[str]:
    if selected_stages:
        stages = validate_stage_names(selected_stages)
        ensure_stage_order(stages)
        return list(stages)

    if stage_from or stage_to:
        if stage_from and stage_from not in STAGE_ORDER:
            raise ValueError(f"Unknown --from stage: {stage_from}")
        if stage_to and stage_to not in STAGE_ORDER:
            raise ValueError(f"Unknown --to stage: {stage_to}")
        start_idx = STAGE_ORDER.index(stage_from) if stage_from else 0
        end_idx = STAGE_ORDER.index(stage_to) if stage_to else len(STAGE_ORDER) - 1
        if start_idx > end_idx:
            raise ValueError("--from stage must be before --to stage")
        return STAGE_ORDER[start_idx : end_idx + 1]

    return list(STAGE_ORDER)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_pipeline(
    *,
    run_dir: Path,
    run_id: str,
    config: AppConfig,
    logger: logging.Logger,
    metrics_path: Path,
    stage_plan: Sequence[str],
    options: PipelineOptions,
    stage_definitions: Sequence[StageDefinition] | None = None,
) -> int:
    if options.artifact_validation not in {"strict", "lenient"}:
        log_event(logger, logging.ERROR, "config_invalid", "Invalid artifact_validation mode")
        return EXIT_CONFIG_ERROR

    if "score" in stage_plan and not config.sampling.raw.enabled:
        log_event(
            logger,
            logging.ERROR,
            "config_invalid",
            "score stage requires sampling.raw.enabled=true",
        )
        return EXIT_CONFIG_ERROR

    definitions = stage_definitions or default_stage_definitions(config)
    stage_map = {stage.name: stage for stage in definitions}
    missing = [name for name in stage_plan if name not in stage_map]
    if missing:
        log_event(logger, logging.ERROR, "config_invalid", f"Missing stage definitions: {missing}")
        return EXIT_CONFIG_ERROR

    inputs_by_stage = {stage.name: list(stage.inputs) for stage in definitions}
    outputs_by_stage = {stage.name: list(stage.outputs) for stage in definitions}
    state_path = run_dir / "pipeline_state.json"

    if state_path.exists():
        try:
            state = load_pipeline_state(state_path, expected_spec=PIPELINE_SPEC_VERSION)
        except SpecVersionMismatchError as exc:
            log_event(logger, logging.ERROR, "state_incompatible", str(exc))
            return EXIT_VALIDATION_ERROR
    else:
        state = create_pipeline_state(
            run_id,
            stage_map.keys(),
            scanner_version=__version__,
            spec_version=PIPELINE_SPEC_VERSION,
            inputs_by_stage=inputs_by_stage,
            outputs_by_stage=outputs_by_stage,
        )
        write_pipeline_state(state_path, state)

    ctx = StageContext(
        run_dir=run_dir,
        config=config,
        logger=logger,
        client=MexcClient(config.mexc, logger=logger, run_id=run_id),
        metrics_path=metrics_path,
        artifact_validation=options.artifact_validation,
    )

    log_event(
        logger,
        logging.INFO,
        "pipeline_plan",
        "Pipeline plan built",
        stages=list(stage_plan),
        resume=options.resume,
        force=options.force,
        dry_run=options.dry_run,
    )

    try:
        if options.dry_run:
            for name in stage_plan:
                stage = stage_map[name]
                errors = stage.validate_inputs(ctx) + stage.validate_outputs(ctx)
                log_event(
                    logger,
                    logging.INFO,
                    "stage_check",
                    "Stage preconditions checked",
                    stage=name,
                    ok=not errors,
                    errors=errors,
                )
            return EXIT_OK

        failed = False
        exit_code = EXIT_OK
        for name in stage_plan:
            stage = stage_map[name]
            input_errors = stage.validate_inputs(ctx)
            if input_errors:
                state.set_stage(
                    name,
                    status="failed",
                    started_at=_now_iso(),
                    finished_at=_now_iso(),
                    error={"type": "ArtifactValidationError", "message": "; ".join(input_errors)},
                )
                write_pipeline_state(state_path, state)
                log_event(
                    logger,
                    logging.ERROR,
                    "stage_fail",
                    "Stage preconditions failed",
                    stage=name,
                    errors=input_errors,
                )
                return EXIT_VALIDATION_ERROR

            output_errors = stage.validate_outputs(ctx)
            if options.resume and not options.force and not output_errors:
                state.set_stage(
                    name,
                    status="skipped",
                    started_at=None,
                    finished_at=_now_iso(),
                    metrics={},
                    error=None,
                )
                write_pipeline_state(state_path, state)
                update_metrics(metrics_path, increments={"pipeline_stage_skipped_total": 1})
                log_event(logger, logging.INFO, "stage_skip", "Stage skipped", stage=name)
                continue

            state.set_stage(name, status="running", started_at=_now_iso(), finished_at=None, error=None)
            write_pipeline_state(state_path, state)
            log_event(logger, logging.INFO, "stage_start", "Stage started", stage=name)

            start = time.monotonic()
            try:
                metrics = stage.run(ctx) or {}
            except Exception as exc:  # noqa: BLE001
                duration_ms = round((time.monotonic() - start) * 1000, 2)
                state.set_stage(
                    name,
                    status="failed",
                    finished_at=_now_iso(),
                    metrics={"duration_ms": duration_ms},
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
                write_pipeline_state(state_path, state)
                update_metrics(metrics_path, increments={"pipeline_stage_failed_total": 1})
                log_event(
                    logger,
                    logging.ERROR,
                    "stage_fail",
                    "Stage failed",
                    stage=name,
                    duration_ms=duration_ms,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                failed = True
                exit_code = max(exit_code, EXIT_STAGE_ERROR)
                if options.continue_on_error or not options.fail_fast:
                    continue
                return EXIT_STAGE_ERROR

            duration_ms = round((time.monotonic() - start) * 1000, 2)
            output_errors = stage.validate_outputs(ctx)
            if output_errors:
                state.set_stage(
                    name,
                    status="failed",
                    finished_at=_now_iso(),
                    metrics={"duration_ms": duration_ms, **metrics},
                    error={"type": "ArtifactValidationError", "message": "; ".join(output_errors)},
                )
                write_pipeline_state(state_path, state)
                update_metrics(metrics_path, increments={"pipeline_stage_failed_total": 1})
                log_event(
                    logger,
                    logging.ERROR,
                    "stage_fail",
                    "Stage outputs invalid",
                    stage=name,
                    duration_ms=duration_ms,
                    errors=output_errors,
                )
                failed = True
                exit_code = max(exit_code, EXIT_VALIDATION_ERROR)
                if options.continue_on_error or not options.fail_fast:
                    continue
                return EXIT_VALIDATION_ERROR

            state.set_stage(
                name,
                status="success",
                finished_at=_now_iso(),
                metrics={"duration_ms": duration_ms, **metrics},
                error=None,
            )
            write_pipeline_state(state_path, state)
            update_metrics(metrics_path, increments={"pipeline_stage_success_total": 1})
            log_event(
                logger,
                logging.INFO,
                "stage_success",
                "Stage finished",
                stage=name,
                duration_ms=duration_ms,
                outputs=list(stage.outputs),
            )

        if failed and exit_code == EXIT_OK:
            exit_code = EXIT_STAGE_ERROR

        log_event(
            logger,
            logging.INFO,
            "pipeline_done",
            "Pipeline completed",
            failed=failed,
            exit_code=exit_code,
        )
        return exit_code
    finally:
        ctx.client.close()
