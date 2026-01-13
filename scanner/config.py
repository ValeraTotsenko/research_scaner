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


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_name: str | None = Field(default=None)
    timezone: str = Field(default="UTC")


class ObsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_jsonl: bool = Field(default=True)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mexc: MexcConfig = Field(default_factory=MexcConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    obs: ObsConfig = Field(default_factory=ObsConfig)


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
