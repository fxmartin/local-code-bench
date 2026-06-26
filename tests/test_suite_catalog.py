"""Tests for the available-suites catalog and custom-suite registration."""

from __future__ import annotations

import gzip
import json

import pytest

from local_code_bench.config import ConfigError
from local_code_bench.suite_catalog import (
    BUILTIN_SUITE_IDS,
    SuiteCatalogEntry,
    builtin_suite_ids,
    catalog_payload,
    load_custom_suites,
    suite_catalog,
)


def _write_humaneval_cache(cache, count: int = 164) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    with gzip.open(cache / "HumanEval.jsonl.gz", "wt", encoding="utf-8") as file:
        for index in range(count):
            file.write(
                json.dumps(
                    {
                        "task_id": f"HumanEval/{index}",
                        "prompt": "def add(a, b):\n",
                        "entry_point": "add",
                        "test": "def check(fn):\n    assert fn(1, 2) == 3",
                    }
                )
                + "\n"
            )


def _write_mbpp_cache(cache, count: int = 3) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "sanitized-mbpp.json").write_text(
        json.dumps(
            [
                {
                    "task_id": index,
                    "prompt": "Write add.",
                    "test_list": ["assert add(1, 2) == 3"],
                }
                for index in range(count)
            ]
        ),
        encoding="utf-8",
    )


def _write_evalplus_cache(cache, name: str = "HumanEvalPlus.jsonl") -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / name).write_text(
        json.dumps(
            {
                "task_id": "HumanEval/0",
                "entry_point": "add",
                "prompt": "def add(a, b):\n",
                "canonical_solution": "    return a + b\n",
                "base_input": [[1, 2]],
                "plus_input": [[3, 4]],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _entries_by_id(entries: list[SuiteCatalogEntry]) -> dict[str, SuiteCatalogEntry]:
    return {entry.id: entry for entry in entries}


def test_builtin_suites_listed_with_identity_and_counts(tmp_path) -> None:
    cache = tmp_path / "benchmarks"
    _write_humaneval_cache(cache)
    _write_mbpp_cache(cache, count=7)

    entries = _entries_by_id(suite_catalog(cache_dir=cache, suites_path=tmp_path / "missing.yaml"))

    # Every built-in is present and identified.
    assert set(BUILTIN_SUITE_IDS) <= set(entries)
    for suite_id in BUILTIN_SUITE_IDS:
        assert entries[suite_id].kind == "builtin"
        assert entries[suite_id].label

    # Known counts surface; unknown counts stay None rather than guessing.
    assert entries["humaneval"].task_count == 164
    assert entries["canary"].task_count == 20
    assert entries["mbpp"].task_count == 7
    assert entries["humaneval"].available is True
    assert entries["canary"].available is True
    assert entries["mbpp"].available is True


def test_downloadable_suites_available_without_cache(tmp_path) -> None:
    # No cache files: auto-download suites are still available; their count is
    # only reported when known without a network round-trip.
    entries = _entries_by_id(
        suite_catalog(cache_dir=tmp_path / "empty", suites_path=tmp_path / "x.yaml")
    )

    assert entries["humaneval"].available is True
    assert entries["humaneval"].task_count == 164
    assert entries["canary"].available is True
    assert entries["canary"].task_count == 20
    assert entries["mbpp"].available is True
    assert entries["mbpp"].task_count is None


def test_evalplus_unavailable_when_cache_missing(tmp_path) -> None:
    entries = _entries_by_id(
        suite_catalog(cache_dir=tmp_path / "empty", suites_path=tmp_path / "x.yaml")
    )

    plus = entries["humaneval-plus"]
    assert plus.available is False
    assert plus.task_count is None
    assert plus.reason
    assert "cache" in plus.reason.lower()


def test_evalplus_available_when_cache_present(tmp_path) -> None:
    cache = tmp_path / "benchmarks"
    _write_humaneval_cache(cache)
    _write_evalplus_cache(cache)

    entries = _entries_by_id(suite_catalog(cache_dir=cache, suites_path=tmp_path / "x.yaml"))

    plus = entries["humaneval-plus"]
    assert plus.available is True
    assert plus.task_count == 1
    assert plus.reason is None


def test_custom_suite_appears_from_config(tmp_path) -> None:
    dataset = tmp_path / "my-suite.jsonl"
    dataset.write_text('{"task_id": "a"}\n{"task_id": "b"}\n', encoding="utf-8")
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text(
        "suites:\n  - id: my-suite\n    label: My Suite\n    source: my-suite.jsonl\n",
        encoding="utf-8",
    )

    entries = _entries_by_id(suite_catalog(cache_dir=tmp_path / "empty", suites_path=suites_yaml))

    custom = entries["my-suite"]
    assert custom.kind == "custom"
    assert custom.label == "My Suite"
    assert custom.available is True
    assert custom.task_count == 2
    assert custom.source == "my-suite.jsonl"


def test_custom_suite_unavailable_when_source_missing(tmp_path) -> None:
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text(
        "suites:\n  - id: ghost\n    source: nowhere.jsonl\n",
        encoding="utf-8",
    )

    entries = _entries_by_id(suite_catalog(cache_dir=tmp_path / "empty", suites_path=suites_yaml))

    ghost = entries["ghost"]
    assert ghost.available is False
    assert ghost.task_count is None
    assert ghost.reason
    assert "not found" in ghost.reason.lower()


def test_custom_json_list_dataset_counts(tmp_path) -> None:
    dataset = tmp_path / "data.json"
    dataset.write_text(
        json.dumps([{"task_id": 1}, {"task_id": 2}, {"task_id": 3}]), encoding="utf-8"
    )
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text(
        "suites:\n  - id: jsonlist\n    source: data.json\n",
        encoding="utf-8",
    )

    entries = _entries_by_id(suite_catalog(cache_dir=tmp_path / "empty", suites_path=suites_yaml))

    assert entries["jsonlist"].available is True
    assert entries["jsonlist"].task_count == 3


def test_custom_json_data_object_counts(tmp_path) -> None:
    dataset = tmp_path / "data.json"
    dataset.write_text(json.dumps({"data": [{"x": 1}, {"x": 2}]}), encoding="utf-8")
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text(
        "suites:\n  - id: dataobj\n    source: data.json\n    format: json\n",
        encoding="utf-8",
    )

    entries = _entries_by_id(suite_catalog(cache_dir=tmp_path / "empty", suites_path=suites_yaml))

    assert entries["dataobj"].available is True
    assert entries["dataobj"].task_count == 2


def test_custom_unsupported_format_is_disabled(tmp_path) -> None:
    dataset = tmp_path / "data.txt"
    dataset.write_text("not a dataset", encoding="utf-8")
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text(
        "suites:\n  - id: weird\n    source: data.txt\n",
        encoding="utf-8",
    )

    entries = _entries_by_id(suite_catalog(cache_dir=tmp_path / "empty", suites_path=suites_yaml))

    weird = entries["weird"]
    assert weird.available is False
    assert weird.reason and "could not read dataset" in weird.reason


def test_empty_suites_file_yields_only_builtins(tmp_path) -> None:
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text("", encoding="utf-8")
    entries = suite_catalog(cache_dir=tmp_path / "empty", suites_path=suites_yaml)
    assert {entry.id for entry in entries} == set(BUILTIN_SUITE_IDS)


def test_missing_suites_file_yields_only_builtins(tmp_path) -> None:
    entries = suite_catalog(cache_dir=tmp_path / "empty", suites_path=tmp_path / "nope.yaml")
    assert {entry.id for entry in entries} == set(BUILTIN_SUITE_IDS)


def test_load_custom_suites_rejects_duplicate_id(tmp_path) -> None:
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text(
        "suites:\n  - id: dup\n    source: a.jsonl\n  - id: dup\n    source: b.jsonl\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_custom_suites(suites_yaml)


def test_load_custom_suites_rejects_builtin_id_collision(tmp_path) -> None:
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text(
        "suites:\n  - id: humaneval\n    source: a.jsonl\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_custom_suites(suites_yaml)


def test_load_custom_suites_requires_mapping_list(tmp_path) -> None:
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text("suites: not-a-list\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_custom_suites(suites_yaml)


def test_catalog_payload_is_json_serializable_and_secret_free(tmp_path) -> None:
    cache = tmp_path / "benchmarks"
    _write_humaneval_cache(cache)
    payload = catalog_payload(cache_dir=cache, suites_path=tmp_path / "x.yaml")

    text = json.dumps(payload)  # must not raise
    assert "suites" in payload
    assert isinstance(payload["suites"], list)
    # No host paths / secrets leak through the payload.
    assert str(tmp_path) not in text
    assert "api_key" not in text.lower()


def test_mbpp_count_none_when_cache_malformed(tmp_path) -> None:
    # A present but unparseable MBPP cache must not crash the catalog: the suite
    # stays available (downloadable) with an unknown count rather than guessing.
    cache = tmp_path / "benchmarks"
    cache.mkdir(parents=True, exist_ok=True)
    # Valid JSON but the wrong shape (no list / no 'data' list) makes load_mbpp
    # raise TaskLoadError, which the catalog swallows into an unknown count.
    (cache / "sanitized-mbpp.json").write_text(json.dumps({"unexpected": 1}), encoding="utf-8")

    entries = _entries_by_id(suite_catalog(cache_dir=cache, suites_path=tmp_path / "x.yaml"))

    assert entries["mbpp"].available is True
    assert entries["mbpp"].task_count is None


def test_evalplus_disabled_when_cache_malformed(tmp_path) -> None:
    # The release file is present but unloadable: the suite is disabled and the
    # loader's error is surfaced as the reason instead of failing at launch.
    cache = tmp_path / "benchmarks"
    cache.mkdir(parents=True, exist_ok=True)
    # Valid JSON line but with no test inputs makes load_evalplus raise
    # TaskLoadError, which the catalog surfaces as the disabled reason.
    (cache / "HumanEvalPlus.jsonl").write_text(
        json.dumps(
            {
                "task_id": "HumanEval/0",
                "entry_point": "add",
                "prompt": "def add(a, b):\n",
                "canonical_solution": "    return a + b\n",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    entries = _entries_by_id(suite_catalog(cache_dir=cache, suites_path=tmp_path / "x.yaml"))

    plus = entries["humaneval-plus"]
    assert plus.available is False
    assert plus.task_count is None
    assert plus.reason


def test_custom_json_object_without_data_list_is_disabled(tmp_path) -> None:
    dataset = tmp_path / "data.json"
    dataset.write_text(json.dumps({"not_data": 1}), encoding="utf-8")
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text(
        "suites:\n  - id: bad\n    source: data.json\n",
        encoding="utf-8",
    )

    entries = _entries_by_id(suite_catalog(cache_dir=tmp_path / "empty", suites_path=suites_yaml))

    bad = entries["bad"]
    assert bad.available is False
    assert bad.reason and "could not read dataset" in bad.reason


def test_custom_explicit_unsupported_format_is_disabled(tmp_path) -> None:
    dataset = tmp_path / "data.bin"
    dataset.write_text("anything", encoding="utf-8")
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text(
        "suites:\n  - id: csvish\n    source: data.bin\n    format: csv\n",
        encoding="utf-8",
    )

    entries = _entries_by_id(suite_catalog(cache_dir=tmp_path / "empty", suites_path=suites_yaml))

    csvish = entries["csvish"]
    assert csvish.available is False
    assert csvish.reason and "could not read dataset" in csvish.reason


def test_load_custom_suites_rejects_invalid_yaml(tmp_path) -> None:
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text("suites: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_custom_suites(suites_yaml)


def test_load_custom_suites_rejects_non_mapping_entry(tmp_path) -> None:
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text("suites:\n  - just-a-string\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_custom_suites(suites_yaml)


def test_load_custom_suites_rejects_missing_id(tmp_path) -> None:
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text("suites:\n  - source: a.jsonl\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_custom_suites(suites_yaml)


def test_load_custom_suites_rejects_non_string_label(tmp_path) -> None:
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text(
        "suites:\n  - id: a\n    source: a.jsonl\n    label: 123\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_custom_suites(suites_yaml)


def test_load_custom_suites_rejects_non_string_format(tmp_path) -> None:
    suites_yaml = tmp_path / "suites.yaml"
    suites_yaml.write_text(
        "suites:\n  - id: a\n    source: a.jsonl\n    format: [json]\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_custom_suites(suites_yaml)


def test_builtin_suite_ids_returns_presentation_order() -> None:
    assert tuple(builtin_suite_ids()) == BUILTIN_SUITE_IDS


def test_entry_to_dict_round_trips_fields() -> None:
    entry = SuiteCatalogEntry(
        id="x",
        label="X",
        kind="builtin",
        available=False,
        task_count=None,
        reason="missing",
        source=None,
    )
    assert entry.to_dict() == {
        "id": "x",
        "label": "X",
        "kind": "builtin",
        "available": False,
        "task_count": None,
        "reason": "missing",
        "source": None,
    }
