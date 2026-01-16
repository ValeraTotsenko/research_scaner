import json
import zipfile
from pathlib import Path

from scanner.config import AppConfig
from scanner.io.bundle import create_run_bundle


def test_bundle_includes_expected_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_1"
    run_dir.mkdir()

    (run_dir / "summary.csv").write_text("symbol,score\nAAA,1\n", encoding="utf-8")
    (run_dir / "summary.json").write_text("[]", encoding="utf-8")
    (run_dir / "depth_metrics.csv").write_text(
        "symbol,pass_depth,uptime,depth_fail_reasons\n",
        encoding="utf-8",
    )
    (run_dir / "summary_enriched.csv").write_text(
        "symbol,score,pass_spread,pass_depth,pass_total,depth_fail_reasons\n",
        encoding="utf-8",
    )
    (run_dir / "report.md").write_text("# Report\n", encoding="utf-8")
    (run_dir / "shortlist.csv").write_text("symbol,score\n", encoding="utf-8")

    run_meta = {"run_id": "run_1", "started_at": "2024-01-01T00:00:00Z", "config": {"foo": "bar"}}
    (run_dir / "run_meta.json").write_text(json.dumps(run_meta), encoding="utf-8")

    bundle_path = create_run_bundle(run_dir, AppConfig())

    with zipfile.ZipFile(bundle_path, "r") as bundle:
        names = set(bundle.namelist())

    assert "summary.csv" in names
    assert "summary.json" in names
    assert "depth_metrics.csv" in names
    assert "summary_enriched.csv" in names
    assert "run_meta.json" in names
    assert "report.md" in names
    assert "shortlist.csv" in names
    assert "run_config.json" in names
