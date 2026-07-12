"""load_suite resolution of custom suites registered in configs/suites.yaml."""

from __future__ import annotations

import json

import pytest

from local_code_bench.tasks import TaskLoadError, load_suite


def _write_registry(tmp_path, body: str):
    registry = tmp_path / "suites.yaml"
    registry.write_text(body, encoding="utf-8")
    return registry


def _record(**overrides) -> dict:
    record = {
        "task_id": "mini/0",
        "prompt": "Write a function add(a, b).",
        "test_code": "assert add(1, 2) == 3",
        "entry_point": "add",
        "version": "mini-v1",
    }
    record.update(overrides)
    return record


def test_load_suite_resolves_registered_custom_suite(tmp_path) -> None:
    (tmp_path / "mini.jsonl").write_text(json.dumps(_record()) + "\n", encoding="utf-8")
    registry = _write_registry(tmp_path, "suites:\n  - id: mini\n    source: mini.jsonl\n")

    tasks = load_suite("mini", cache_dir=tmp_path, suites_path=registry)

    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_id == "mini/0"
    assert task.suite == "mini"
    assert task.prompt.startswith("Write a function")
    assert task.test_code == "assert add(1, 2) == 3"
    assert task.entry_point == "add"
    assert task.version == "mini-v1"


def test_custom_record_defaults(tmp_path) -> None:
    record = {"prompt": "p", "test_code": "assert True"}
    (tmp_path / "mini.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    registry = _write_registry(tmp_path, "suites:\n  - id: mini\n    source: mini.jsonl\n")

    task = load_suite("mini", cache_dir=tmp_path, suites_path=registry)[0]

    assert task.task_id == "mini/0"
    assert task.entry_point == "solution"
    assert task.version == "custom:mini"


def test_custom_json_list_and_data_object(tmp_path) -> None:
    (tmp_path / "as-list.json").write_text(json.dumps([_record()]), encoding="utf-8")
    (tmp_path / "as-data.json").write_text(json.dumps({"data": [_record()]}), encoding="utf-8")
    registry = _write_registry(
        tmp_path,
        "suites:\n"
        "  - id: as-list\n    source: as-list.json\n"
        "  - id: as-data\n    source: as-data.json\n",
    )

    assert len(load_suite("as-list", cache_dir=tmp_path, suites_path=registry)) == 1
    assert len(load_suite("as-data", cache_dir=tmp_path, suites_path=registry)) == 1


def test_unknown_suite_error_lists_builtins_and_customs(tmp_path) -> None:
    (tmp_path / "mini.jsonl").write_text(json.dumps(_record()) + "\n", encoding="utf-8")
    registry = _write_registry(tmp_path, "suites:\n  - id: mini\n    source: mini.jsonl\n")

    with pytest.raises(TaskLoadError, match="unknown suite 'nope'") as excinfo:
        load_suite("nope", cache_dir=tmp_path, suites_path=registry)

    message = str(excinfo.value)
    assert "humaneval" in message
    assert "mini" in message


def test_unknown_suite_without_registry_still_lists_builtins(tmp_path) -> None:
    with pytest.raises(TaskLoadError, match="canary"):
        load_suite("nope", cache_dir=tmp_path, suites_path=tmp_path / "missing.yaml")


@pytest.mark.parametrize("missing", ["prompt", "test_code"])
def test_custom_record_missing_required_field(tmp_path, missing) -> None:
    record = _record()
    del record[missing]
    (tmp_path / "mini.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    registry = _write_registry(tmp_path, "suites:\n  - id: mini\n    source: mini.jsonl\n")

    with pytest.raises(TaskLoadError, match=f"missing {missing}"):
        load_suite("mini", cache_dir=tmp_path, suites_path=registry)


def test_custom_record_must_be_mapping(tmp_path) -> None:
    (tmp_path / "mini.jsonl").write_text('"just a string"\n', encoding="utf-8")
    registry = _write_registry(tmp_path, "suites:\n  - id: mini\n    source: mini.jsonl\n")

    with pytest.raises(TaskLoadError, match="must be a mapping"):
        load_suite("mini", cache_dir=tmp_path, suites_path=registry)


def test_custom_suite_missing_source_file(tmp_path) -> None:
    registry = _write_registry(tmp_path, "suites:\n  - id: mini\n    source: gone.jsonl\n")

    with pytest.raises(TaskLoadError, match="source not found"):
        load_suite("mini", cache_dir=tmp_path, suites_path=registry)


def test_custom_suite_empty_dataset(tmp_path) -> None:
    (tmp_path / "mini.jsonl").write_text("", encoding="utf-8")
    registry = _write_registry(tmp_path, "suites:\n  - id: mini\n    source: mini.jsonl\n")

    with pytest.raises(TaskLoadError, match="contained no tasks"):
        load_suite("mini", cache_dir=tmp_path, suites_path=registry)


def test_invalid_registry_maps_to_task_load_error(tmp_path) -> None:
    registry = _write_registry(tmp_path, "suites: 42\n")

    with pytest.raises(TaskLoadError, match="invalid custom-suite registry"):
        load_suite("anything", cache_dir=tmp_path, suites_path=registry)


def test_custom_json_dataset_must_hold_a_list(tmp_path) -> None:
    (tmp_path / "mini.json").write_text(json.dumps({"rows": []}), encoding="utf-8")
    registry = _write_registry(tmp_path, "suites:\n  - id: mini\n    source: mini.json\n")

    with pytest.raises(TaskLoadError, match="must be a list"):
        load_suite("mini", cache_dir=tmp_path, suites_path=registry)
