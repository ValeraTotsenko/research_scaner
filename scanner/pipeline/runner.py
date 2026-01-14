from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from scanner import __version__
from scanner.config import AppConfig
from scanner.mexc.client import MexcClient
from scanner.obs.logging import log_event
from scanner.obs.metrics import update_http_metrics, update_metrics
from scanner.pipeline.errors import StageTimeoutError
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


def _min_ticks_success(stage_name: str, config: AppConfig) -> int | None:
    if stage_name == "spread":
        spread_cfg = config.sampling.spread
        target_ticks = max(1, math.ceil(spread_cfg.duration_s / spread_cfg.interval_s))
        return max(1, math.ceil(target_ticks * spread_cfg.min_uptime))
    if stage_name == "depth":
        return 1
    return None


def _has_minimum_data(stage_name: str, metrics: dict[str, object], config: AppConfig) -> bool:
    ticks_success = metrics.get("ticks_success")
    if ticks_success is None:
        return False
    try:
        ticks_success_value = int(float(ticks_success))
    except (TypeError, ValueError):
        return False
    min_ticks_success = _min_ticks_success(stage_name, config)
    if min_ticks_success is None:
        return False
    return ticks_success_value >= min_ticks_success


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

    def _flush_http_metrics() -> None:
        if ctx.client:
            update_http_metrics(metrics_path, ctx.client.metrics)

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

    run_start = time.monotonic()
    run_deadline = None
    if config.pipeline.total_timeout_s > 0:
        run_deadline = run_start + config.pipeline.total_timeout_s
    run_timed_out = False
    run_degraded = False
    timeout_grace_s = max(0.0, float(config.pipeline.timeout_grace_s))

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
            stage_timeout_s = config.pipeline.stage_timeouts_s.get(name, 0)
            stage_deadline = None
            if stage_timeout_s > 0:
                stage_deadline = time.monotonic() + stage_timeout_s
            if run_deadline is not None:
                stage_deadline = min(stage_deadline, run_deadline) if stage_deadline else run_deadline
            stage_deadline_grace = stage_deadline + timeout_grace_s if stage_deadline else None
            stage_ctx = replace(ctx, stage_deadline_ts=stage_deadline_grace)
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
                _flush_http_metrics()
                log_event(
                    logger,
                    logging.ERROR,
                    "stage_fail",
                    "Stage preconditions failed",
                    stage=name,
                    errors=input_errors,
                )
                log_event(
                    logger,
                    logging.ERROR,
                    "stage_end",
                    "Stage finished",
                    stage=name,
                    status="failed",
                )
                return EXIT_VALIDATION_ERROR

            output_errors = stage.validate_outputs(ctx)
            stage_state = state.get_stage(name)
            stage_previously_timed_out = bool(stage_state.metrics.get("timed_out")) or (
                stage_state.error and stage_state.error.get("type") == StageTimeoutError.__name__
            )
            if (
                options.resume
                and not options.force
                and not output_errors
                and not stage_previously_timed_out
            ):
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
                _flush_http_metrics()
                log_event(logger, logging.INFO, "stage_skip", "Stage skipped", stage=name)
                continue

            state.set_stage(name, status="running", started_at=_now_iso(), finished_at=None, error=None)
            write_pipeline_state(state_path, state)
            log_event(logger, logging.INFO, "stage_start", "Stage started", stage=name)

            start = time.monotonic()
            if stage_deadline_grace is not None and time.monotonic() >= stage_deadline_grace:
                elapsed_s = time.monotonic() - start
                timeout_s = max(0.0, stage_deadline - start) if stage_deadline else 0.0
                error_payload = {
                    "type": StageTimeoutError.__name__,
                    "stage": name,
                    "timeout_s": timeout_s,
                    "elapsed_s": elapsed_s,
                }
                state.set_stage(
                    name,
                    status="failed",
                    finished_at=_now_iso(),
                    metrics={"timed_out": True, "duration_ms": round(elapsed_s * 1000, 2)},
                    error=error_payload,
                )
                write_pipeline_state(state_path, state)
                update_metrics(
                    metrics_path,
                    increments={
                        "pipeline_stage_timeouts_total": 1,
                        "pipeline_stage_failed_total": 1,
                        **(
                            {"pipeline_run_timeouts_total": 1}
                            if run_deadline is not None and stage_deadline == run_deadline and not run_timed_out
                            else {}
                        ),
                    },
                    gauges={f"stage_elapsed_seconds.{name}": round(elapsed_s, 2)},
                )
                _flush_http_metrics()
                run_timed_out = run_timed_out or (run_deadline is not None and stage_deadline == run_deadline)
                log_event(
                    logger,
                    logging.ERROR,
                    "stage_timeout",
                    "Stage deadline exceeded before start",
                    stage=name,
                    elapsed_s=round(elapsed_s, 2),
                    timeout_s=timeout_s,
                    action="fail",
                )
                log_event(
                    logger,
                    logging.ERROR,
                    "stage_end",
                    "Stage finished",
                    stage=name,
                    status="failed",
                    duration_ms=round(elapsed_s * 1000, 2),
                )
                failed = True
                exit_code = max(exit_code, EXIT_STAGE_ERROR)
                if options.continue_on_error or not options.fail_fast:
                    continue
                return EXIT_STAGE_ERROR
            try:
                metrics = stage.run(stage_ctx) or {}
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
                update_metrics(
                    metrics_path,
                    increments={"pipeline_stage_failed_total": 1},
                    gauges={f"stage_elapsed_seconds.{name}": round(duration_ms / 1000, 2)},
                )
                _flush_http_metrics()
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
                log_event(
                    logger,
                    logging.ERROR,
                    "stage_end",
                    "Stage finished",
                    stage=name,
                    status="failed",
                    duration_ms=duration_ms,
                )
                failed = True
                exit_code = max(exit_code, EXIT_STAGE_ERROR)
                if options.continue_on_error or not options.fail_fast:
                    continue
                return EXIT_STAGE_ERROR

            duration_ms = round((time.monotonic() - start) * 1000, 2)
            elapsed_s = duration_ms / 1000
            timeout_s = max(0.0, stage_deadline - start) if stage_deadline else 0.0
            timed_out = bool(metrics.get("timed_out")) or (
                stage_deadline is not None and elapsed_s > timeout_s
            )
            if timed_out:
                output_errors = stage.validate_outputs(ctx)
                has_outputs = not output_errors
                has_minimum_data = _has_minimum_data(name, metrics, config)
                error_payload = {
                    "type": StageTimeoutError.__name__,
                    "stage": name,
                    "timeout_s": timeout_s,
                    "elapsed_s": elapsed_s,
                    "output_errors": output_errors,
                    "has_minimum_data": has_minimum_data,
                }
                action = "partial_success" if has_outputs and has_minimum_data else "fail"
                status = "timeout" if action == "partial_success" else "failed"
                state.set_stage(
                    name,
                    status=status,
                    finished_at=_now_iso(),
                    metrics={"duration_ms": duration_ms, "timed_out": True, **metrics},
                    error=error_payload,
                )
                write_pipeline_state(state_path, state)
                update_metrics(
                    metrics_path,
                    increments={
                        "pipeline_stage_timeouts_total": 1,
                        **(
                            {"pipeline_run_timeouts_total": 1}
                            if run_deadline is not None and stage_deadline == run_deadline and not run_timed_out
                            else {}
                        ),
                        **(
                            {"pipeline_stage_success_total": 1}
                            if action == "partial_success"
                            else {"pipeline_stage_failed_total": 1}
                        ),
                    },
                    gauges={f"stage_elapsed_seconds.{name}": round(elapsed_s, 2)},
                )
                _flush_http_metrics()
                run_timed_out = run_timed_out or (run_deadline is not None and stage_deadline == run_deadline)
                log_event(
                    logger,
                    logging.ERROR if action == "fail" else logging.WARNING,
                    "stage_timeout",
                    "Stage deadline exceeded",
                    stage=name,
                    elapsed_s=round(elapsed_s, 2),
                    timeout_s=timeout_s,
                    action=action,
                )
                log_event(
                    logger,
                    logging.ERROR if action == "fail" else logging.WARNING,
                    "stage_end",
                    "Stage finished",
                    stage=name,
                    status=status,
                    duration_ms=duration_ms,
                )
                if action == "partial_success":
                    run_degraded = True
                    continue
                failed = True
                exit_code = max(exit_code, EXIT_STAGE_ERROR)
                if options.continue_on_error or not options.fail_fast:
                    continue
                return EXIT_STAGE_ERROR

            update_metrics(metrics_path, gauges={f"stage_elapsed_seconds.{name}": round(elapsed_s, 2)})
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
                _flush_http_metrics()
                log_event(
                    logger,
                    logging.ERROR,
                    "stage_fail",
                    "Stage outputs invalid",
                    stage=name,
                    duration_ms=duration_ms,
                    errors=output_errors,
                )
                log_event(
                    logger,
                    logging.ERROR,
                    "stage_end",
                    "Stage finished",
                    stage=name,
                    status="failed",
                    duration_ms=duration_ms,
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
            _flush_http_metrics()
            log_event(
                logger,
                logging.INFO,
                "stage_success",
                "Stage finished",
                stage=name,
                duration_ms=duration_ms,
                outputs=list(stage.outputs),
            )
            log_event(
                logger,
                logging.INFO,
                "stage_end",
                "Stage finished",
                stage=name,
                status="success",
                duration_ms=duration_ms,
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
            status="success_with_warnings" if run_degraded and not failed else ("failed" if failed else "success"),
        )
        return exit_code
    finally:
        _flush_http_metrics()
        ctx.client.close()
