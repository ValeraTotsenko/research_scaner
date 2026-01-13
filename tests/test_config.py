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
