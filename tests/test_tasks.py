from __future__ import annotations

import gzip
import json

import pytest

from local_code_bench.tasks import TaskLoadError, limit_tasks, load_humaneval, load_mbpp, load_suite


def test_load_humaneval_from_cache(tmp_path) -> None:
    cache = tmp_path / "benchmarks"
    cache.mkdir()
    path = cache / "HumanEval.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as file:
        for index in range(164):
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

    tasks = load_humaneval(cache_dir=cache)

    assert len(tasks) == 164
    assert tasks[0].task_id == "HumanEval/0"
    assert "check(add)" in tasks[0].test_code


def test_load_mbpp_from_cache(tmp_path) -> None:
    cache = tmp_path / "benchmarks"
    cache.mkdir()
    (cache / "sanitized-mbpp.json").write_text(
        json.dumps(
            [
                {
                    "task_id": 1,
                    "prompt": "Write add.",
                    "test_list": ["assert add(1, 2) == 3"],
                }
            ]
        ),
        encoding="utf-8",
    )

    tasks = load_mbpp(cache_dir=cache)

    assert tasks[0].task_id == "mbpp/1"
    assert tasks[0].entry_point == "add"
    assert "Define a Python function named `add`" in tasks[0].prompt
    assert "assert add(1, 2) == 3" in tasks[0].prompt


def test_load_mbpp_extracts_wrapped_entry_point_from_tests(tmp_path) -> None:
    cache = tmp_path / "benchmarks"
    cache.mkdir()
    (cache / "sanitized-mbpp.json").write_text(
        json.dumps(
            [
                {
                    "task_id": 2,
                    "prompt": "Write a function to find similar elements.",
                    "test_list": [
                        "assert set(similar_elements((3, 4, 5, 6),(5, 7, 4, 10))) == set((4, 5))"
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    tasks = load_mbpp(cache_dir=cache)

    assert tasks[0].entry_point == "similar_elements"
    assert "Define a Python function named `similar_elements`" in tasks[0].prompt


def test_load_suite_reports_unknown_suite() -> None:
    with pytest.raises(TaskLoadError, match="unknown suite"):
        load_suite("unknown")


def test_limit_tasks_rejects_negative_limit() -> None:
    with pytest.raises(TaskLoadError, match="limit must be non-negative"):
        limit_tasks([], -1)


def test_load_humaneval_rejects_wrong_cache_size(tmp_path) -> None:
    cache = tmp_path / "benchmarks"
    cache.mkdir()
    path = cache / "HumanEval.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as file:
        file.write(
            json.dumps(
                {
                    "task_id": "HumanEval/0",
                    "prompt": "def add(a, b):\n",
                    "entry_point": "add",
                    "test": "def check(fn):\n    assert fn(1, 2) == 3",
                }
            )
            + "\n"
        )

    with pytest.raises(TaskLoadError, match="expected 164"):
        load_humaneval(cache_dir=cache)


def test_load_mbpp_rejects_invalid_cache_shape(tmp_path) -> None:
    cache = tmp_path / "benchmarks"
    cache.mkdir()
    (cache / "sanitized-mbpp.json").write_text(json.dumps({"data": "bad"}), encoding="utf-8")

    with pytest.raises(TaskLoadError, match="must be a list"):
        load_mbpp(cache_dir=cache)


def test_load_mbpp_rejects_missing_tests(tmp_path) -> None:
    cache = tmp_path / "benchmarks"
    cache.mkdir()
    (cache / "sanitized-mbpp.json").write_text(
        json.dumps([{"task_id": 3, "prompt": "Write something."}]),
        encoding="utf-8",
    )

    with pytest.raises(TaskLoadError, match="missing test_list"):
        load_mbpp(cache_dir=cache)
