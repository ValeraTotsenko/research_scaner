from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO


@dataclass(frozen=True)
class RawWriterPaths:
    raw_path: Path


class RawJsonlWriter:
    def __init__(self, path: Path, *, gzip_enabled: bool) -> None:
        self._path = path
        self._gzip_enabled = gzip_enabled
        self._handle: TextIO | None = None

    @property
    def path(self) -> Path:
        return self._path

    def __enter__(self) -> "RawJsonlWriter":
        if self._gzip_enabled:
            self._handle = gzip.open(self._path, "at", encoding="utf-8")
        else:
            self._handle = self._path.open("a", encoding="utf-8")
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def write(self, record: dict[str, Any]) -> None:
        if not self._handle:
            raise RuntimeError("Writer not opened")
        payload = json.dumps(record, ensure_ascii=False)
        self._handle.write(f"{payload}\n")

    def close(self) -> None:
        if self._handle:
            self._handle.close()
            self._handle = None


def create_raw_bookticker_writer(output_dir: Path, *, gzip_enabled: bool) -> RawJsonlWriter:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "jsonl.gz" if gzip_enabled else "jsonl"
    raw_path = output_dir / f"raw_bookticker.{suffix}"
    return RawJsonlWriter(raw_path, gzip_enabled=gzip_enabled)
