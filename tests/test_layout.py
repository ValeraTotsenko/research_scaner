from pathlib import Path

from scanner.config import AppConfig
from scanner.io.layout import create_run_layout


def test_layout_creates_files(tmp_path: Path) -> None:
    config = AppConfig()
    run_id = "20260113_220501Z_ab12cd"

    layout = create_run_layout(tmp_path, run_id, config)

    assert layout.run_dir.exists()
    assert layout.run_meta_path.name == "run_meta.json"
    assert layout.metrics_path.exists()
    assert layout.log_path and layout.log_path.exists()
