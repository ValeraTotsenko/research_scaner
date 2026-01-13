import os
from pathlib import Path

import pytest

from scanner.config import AppConfig
from scanner.io.layout import create_run_layout


def test_output_read_only(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    output_dir.chmod(0o500)

    config = AppConfig()

    with pytest.raises(PermissionError):
        create_run_layout(output_dir, "20260113_220501Z_ab12cd", config)

    os.chmod(output_dir, 0o700)
