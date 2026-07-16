"""Safe engine-provenance backfill for legacy benchmark JSONL files."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path

from local_code_bench.engine_provenance import EngineProvenance


class BackfillError(ValueError):
    """Raised when a result file cannot be safely backfilled."""


def backfill_jsonl(
    path: str | Path,
    provenance: EngineProvenance,
    *,
    backup_dir: str | Path,
) -> int:
    """Atomically add provenance while preserving every non-engine field."""

    result_path = Path(path)
    original = result_path.read_bytes()
    records = _parse_records(result_path, original)
    updated = deepcopy(records)
    changed = _apply_provenance(updated, provenance)
    if changed == 0:
        return 0
    if _without_engine(records) != _without_engine(updated):
        raise BackfillError(f"{result_path}: non-engine fields changed during backfill")

    backups = Path(backup_dir)
    backups.mkdir(parents=True, exist_ok=True)
    backup_path = backups / result_path.name
    if backup_path.exists() and backup_path.read_bytes() != original:
        raise BackfillError(f"backup already exists with different contents: {backup_path}")
    if not backup_path.exists():
        shutil.copy2(result_path, backup_path)

    _atomic_write(result_path, updated)
    reparsed = _parse_records(result_path, result_path.read_bytes())
    if len(reparsed) != len(records) or _without_engine(reparsed) != _without_engine(records):
        raise BackfillError(f"{result_path}: post-write integrity check failed")
    return changed


def _parse_records(path: Path, content: bytes) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(content.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BackfillError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise BackfillError(f"{path}:{line_number}: JSONL record is not an object")
        records.append(record)
    return records


def _apply_provenance(
    records: list[dict[str, object]], provenance: EngineProvenance
) -> int:
    expected = provenance.as_dict()
    changed = 0
    for record in records:
        if record.get("record_type") == "metadata":
            models = record.get("models")
            if not isinstance(models, dict):
                raise BackfillError("metadata record has no models mapping")
            for details in models.values():
                if not isinstance(details, dict):
                    raise BackfillError("metadata model details are not an object")
                changed += _set_engine(details, expected)
        elif isinstance(record.get("model"), str):
            changed += _set_engine(record, expected)
    return changed


def _set_engine(record: dict[str, object], expected: dict[str, object]) -> int:
    current = record.get("engine")
    if current is None:
        record["engine"] = deepcopy(expected)
        return 1
    if current != expected:
        raise BackfillError("conflicting engine provenance already present")
    return 0


def _without_engine(records: list[dict[str, object]]) -> list[dict[str, object]]:
    cleaned = deepcopy(records)
    for record in cleaned:
        record.pop("engine", None)
        models = record.get("models")
        if isinstance(models, dict):
            for details in models.values():
                if isinstance(details, dict):
                    details.pop("engine", None)
    return cleaned


def _atomic_write(path: Path, records: list[dict[str, object]]) -> None:
    original_mode = path.stat().st_mode
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for record in records:
                json.dump(record, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, original_mode)
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
