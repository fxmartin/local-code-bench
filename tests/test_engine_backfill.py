from __future__ import annotations

import json
import stat

import pytest

from local_code_bench.backfill import BackfillError, backfill_jsonl
from local_code_bench.engine_provenance import EngineProvenance


def _provenance(version: str = "0.32.0") -> EngineProvenance:
    return EngineProvenance(
        name="ollama",
        versions={"ollama": version},
        capture_method="manual-backfill",
    )


def test_backfill_adds_engine_to_metadata_and_result_records(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    records = [
        {
            "record_type": "metadata",
            "models": {"qwen": {"model_id": "qwen:27b", "type": "openai"}},
        },
        {"run_mode": "endpoint", "model": "qwen", "task_id": "HumanEval/0"},
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    path.chmod(0o640)
    backup_dir = tmp_path / "backups"

    changed = backfill_jsonl(path, _provenance(), backup_dir=backup_dir)

    updated = [json.loads(line) for line in path.read_text().splitlines()]
    expected = _provenance().as_dict()
    assert changed == 2
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert updated[0]["models"]["qwen"]["engine"] == expected
    assert updated[1]["engine"] == expected
    assert (backup_dir / "run.jsonl").read_text().splitlines() == [
        json.dumps(record) for record in records
    ]


def test_backfill_handles_metadata_free_sweep_and_is_idempotent(tmp_path) -> None:
    path = tmp_path / "sweep.jsonl"
    path.write_text(json.dumps({"run_mode": "sweep", "model": "qwen"}) + "\n")
    backup_dir = tmp_path / "backups"

    assert backfill_jsonl(path, _provenance(), backup_dir=backup_dir) == 1
    first = path.read_bytes()
    assert backfill_jsonl(path, _provenance(), backup_dir=backup_dir) == 0
    assert path.read_bytes() == first


def test_backfill_refuses_conflicting_engine_without_modifying_file(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    path.write_text(
        json.dumps(
            {
                "run_mode": "endpoint",
                "model": "qwen",
                "engine": _provenance("0.31.0").as_dict(),
            }
        )
        + "\n"
    )
    original = path.read_bytes()

    with pytest.raises(BackfillError, match="conflicting engine provenance"):
        backfill_jsonl(path, _provenance(), backup_dir=tmp_path / "backups")

    assert path.read_bytes() == original


def test_backfill_refuses_invalid_json_without_creating_backup(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    path.write_text("not-json\n")
    backup_dir = tmp_path / "backups"

    with pytest.raises(BackfillError, match="invalid JSON"):
        backfill_jsonl(path, _provenance(), backup_dir=backup_dir)

    assert not backup_dir.exists()
