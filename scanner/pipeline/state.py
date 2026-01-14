from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PIPELINE_SPEC_VERSION = "0.1"


class SpecVersionMismatchError(RuntimeError):
    """Raised when pipeline state spec version does not match current spec."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class StageState:
    name: str
    status: str
    started_at: str | None
    finished_at: str | None
    inputs: list[str]
    outputs: list[str]
    metrics: dict[str, Any]
    error: dict[str, str] | None


@dataclass
class PipelineState:
    run_id: str
    scanner_version: str
    spec_version: str
    stages: list[StageState]
    updated_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scanner_version": self.scanner_version,
            "spec_version": self.spec_version,
            "stages": [
                {
                    "name": stage.name,
                    "status": stage.status,
                    "started_at": stage.started_at,
                    "finished_at": stage.finished_at,
                    "inputs": stage.inputs,
                    "outputs": stage.outputs,
                    "metrics": stage.metrics,
                    "error": stage.error,
                }
                for stage in self.stages
            ],
            "updated_at": self.updated_at,
        }

    def set_stage(
        self,
        name: str,
        *,
        status: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        metrics: dict[str, Any] | None = None,
        error: dict[str, str] | None = None,
    ) -> None:
        stage = self.get_stage(name)
        if status is not None:
            stage.status = status
        if started_at is not None:
            stage.started_at = started_at
        if finished_at is not None:
            stage.finished_at = finished_at
        if metrics is not None:
            stage.metrics = metrics
        if error is not None:
            stage.error = error
        self.updated_at = _now_iso()

    def get_stage(self, name: str) -> StageState:
        for stage in self.stages:
            if stage.name == name:
                return stage
        raise KeyError(f"Stage not found in pipeline state: {name}")


def create_pipeline_state(
    run_id: str,
    stage_names: Iterable[str],
    *,
    scanner_version: str,
    spec_version: str,
    inputs_by_stage: dict[str, list[str]],
    outputs_by_stage: dict[str, list[str]],
) -> PipelineState:
    stages: list[StageState] = []
    for name in stage_names:
        stages.append(
            StageState(
                name=name,
                status="pending",
                started_at=None,
                finished_at=None,
                inputs=list(inputs_by_stage.get(name, [])),
                outputs=list(outputs_by_stage.get(name, [])),
                metrics={},
                error=None,
            )
        )
    return PipelineState(
        run_id=run_id,
        scanner_version=scanner_version,
        spec_version=spec_version,
        stages=stages,
        updated_at=_now_iso(),
    )


def load_pipeline_state(path: Path, *, expected_spec: str) -> PipelineState:
    payload = json.loads(path.read_text(encoding="utf-8"))
    spec_version = payload.get("spec_version")
    if spec_version != expected_spec:
        raise SpecVersionMismatchError(
            f"pipeline_state spec_version mismatch: {spec_version} != {expected_spec}"
        )

    stages_payload = payload.get("stages", [])
    stages: list[StageState] = []
    for stage in stages_payload:
        stages.append(
            StageState(
                name=stage.get("name"),
                status=stage.get("status", "pending"),
                started_at=stage.get("started_at"),
                finished_at=stage.get("finished_at"),
                inputs=list(stage.get("inputs", [])),
                outputs=list(stage.get("outputs", [])),
                metrics=dict(stage.get("metrics", {})),
                error=stage.get("error"),
            )
        )

    return PipelineState(
        run_id=payload.get("run_id", "unknown"),
        scanner_version=payload.get("scanner_version", "unknown"),
        spec_version=spec_version,
        stages=stages,
        updated_at=payload.get("updated_at", _now_iso()),
    )


def write_pipeline_state(path: Path, state: PipelineState) -> None:
    path.write_text(json.dumps(state.to_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
