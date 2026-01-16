from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from scanner.models.universe import UniverseResult


@dataclass(frozen=True)
class UniverseExportPaths:
    universe_path: Path
    rejects_path: Path


def export_universe(output_dir: Path, result: UniverseResult) -> UniverseExportPaths:
    output_dir.mkdir(parents=True, exist_ok=True)

    universe_path = output_dir / "universe.json"
    rejects_path = output_dir / "universe_rejects.csv"

    universe_payload = {
        "symbols": result.symbols,
        "stats": {
            "total": result.stats.total,
            "kept": result.stats.kept,
            "rejected": result.stats.rejected,
        },
        "source_flags": result.source_flags,
    }
    universe_path.write_text(json.dumps(universe_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with rejects_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "reason"])
        writer.writeheader()
        for reject in result.rejects:
            writer.writerow({"symbol": reject.symbol, "reason": reject.reason})

    return UniverseExportPaths(universe_path=universe_path, rejects_path=rejects_path)
