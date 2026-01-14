from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConfigError(Exception):
    """Raised when configuration cannot be loaded or validated."""


class MexcConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(default="https://api.mexc.com")
    timeout_s: float = Field(default=10)
    max_retries: int = Field(default=5)
    backoff_base_s: float = Field(default=0.5)
    backoff_max_s: float = Field(default=8)
    max_rps: float = Field(default=2.0)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_name: str | None = Field(default=None)
    timezone: str = Field(default="UTC")


class ObsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_jsonl: bool = Field(default=True)


class UniverseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_asset: str = Field(default="USDT")
    min_quote_volume_24h: float = Field(default=100_000)
    min_trades_24h: int = Field(default=200)
    blacklist_regex: list[str] = Field(default_factory=list)
    whitelist: list[str] = Field(default_factory=list)


class SpreadSamplingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_s: int = Field(default=1800)
    interval_s: float = Field(default=5)
    min_uptime: float = Field(default=0.9)
    allow_per_symbol: bool = Field(default=False)
    per_symbol_limit: int = Field(default=50)


class DepthSamplingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_s: int = Field(default=1200)
    interval_s: float = Field(default=30)
    limit: int = Field(default=100)


class RawSamplingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True)
    gzip: bool = Field(default=True)


class SamplingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spread: SpreadSamplingConfig = Field(default_factory=SpreadSamplingConfig)
    depth: DepthSamplingConfig = Field(default_factory=DepthSamplingConfig)
    raw: RawSamplingConfig = Field(default_factory=RawSamplingConfig)


class FeesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taker_bps: float = Field(default=4.0)
    maker_bps: float = Field(default=2.0)


class SpreadThresholdsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    median_max_bps: float = Field(default=25.0)
    p90_max_bps: float = Field(default=60.0)


class DepthThresholdsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    best_level_min_notional: float = Field(default=100.0)
    unwind_slippage_max_bps: float = Field(default=50.0)


class DepthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_n_levels: int = Field(default=10)
    band_bps: list[int] = Field(default_factory=lambda: [5, 10, 20])
    stress_notional_usdt: float = Field(default=100.0)


class ThresholdsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spread: SpreadThresholdsConfig = Field(default_factory=SpreadThresholdsConfig)
    depth: DepthThresholdsConfig = Field(default_factory=DepthThresholdsConfig)
    uptime_min: float = Field(default=0.9)


class ReportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_n: int = Field(default=20)
    include_raw_in_bundle: bool = Field(default=False)


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume: bool = Field(default=True)
    fail_fast: bool = Field(default=True)
    continue_on_error: bool = Field(default=False)
    artifact_validation: str = Field(default="strict")


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mexc: MexcConfig = Field(default_factory=MexcConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    obs: ObsConfig = Field(default_factory=ObsConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    fees: FeesConfig = Field(default_factory=FeesConfig)
    depth: DepthConfig = Field(default_factory=DepthConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)


@dataclass(frozen=True)
class LoadedConfig:
    config: AppConfig
    raw: dict[str, Any]


def load_config(path: Path) -> LoadedConfig:
    if not path.exists():
        raise ConfigError(f"Config not found: {path}")

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML: {exc}") from exc

    if not isinstance(payload, dict):
        raise ConfigError("Config root must be a mapping")

    try:
        config = AppConfig.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc

    return LoadedConfig(config=config, raw=payload)
