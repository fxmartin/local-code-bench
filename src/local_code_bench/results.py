"""JSONL result writing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def new_run_path(results_dir: str | Path, *, prefix: str = "run") -> Path:
    """Return a unique JSONL path under the results directory."""

    directory = Path(results_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return directory / f"{prefix}-{stamp}-{uuid4().hex[:8]}.jsonl"


def append_jsonl(path: str | Path, record: dict[str, object]) -> None:
    """Append one JSON record as a single UTF-8 JSONL line."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as file:
        json.dump(record, file, sort_keys=True, separators=(",", ":"))
        file.write("\n")


def read_jsonl(path: str | Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    input_path = Path(path)
    if not input_path.exists():
        return records
    with input_path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                records.append(json.loads(line))
    return records
