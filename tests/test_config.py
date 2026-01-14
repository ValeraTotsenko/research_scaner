import logging
from pathlib import Path

import pytest

from scanner.config import ConfigError, load_config


def test_valid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
        mexc:
          base_url: https://api.mexc.com
        runtime:
          run_name: test
          timezone: UTC
        obs:
          log_jsonl: true
        """,
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    assert loaded.config.mexc.base_url == "https://api.mexc.com"
    assert loaded.config.runtime.run_name == "test"
    assert loaded.config.obs.log_jsonl is True


def test_invalid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
        mexc:
          base_url: 123
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_spread_timeout_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
        sampling:
          spread:
            duration_s: 300
            interval_s: 5
        pipeline:
          stage_timeouts_s:
            spread: 300
          safety_margin_s: 5
          spread_timeout_behavior: warn
        """,
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        load_config(config_path)

    assert "Spread sampling duration_s exceeds the allowed stage timeout buffer" in caplog.text
