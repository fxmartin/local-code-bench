from __future__ import annotations

import gzip
import json

import pytest

from local_code_bench.tasks import (
    CANARY_HUMANEVAL_IDS,
    TaskLoadError,
    limit_tasks,
    load_humaneval,
    load_mbpp,
    load_suite,
)


def _write_humaneval_cache(cache, task_ids) -> None:
    cache.mkdir(exist_ok=True)
    with gzip.open(cache / "HumanEval.jsonl.gz", "wt", encoding="utf-8") as file:
        for task_id in task_ids:
            file.write(
                json.dumps(
                    {
                        "task_id": task_id,
                        "prompt": "def add(a, b):\n",
                        "entry_point": "add",
                        "test": "def check(fn):\n    assert fn(1, 2) == 3",
                    }
                )
                + "\n"
            )


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


def test_load_canary_returns_curated_subset_in_order(tmp_path) -> None:
    cache = tmp_path / "benchmarks"
    _write_humaneval_cache(cache, [f"HumanEval/{index}" for index in range(164)])

    tasks = load_suite("canary", cache_dir=cache)

    assert [task.task_id for task in tasks] == list(CANARY_HUMANEVAL_IDS)
    assert len(tasks) == len(CANARY_HUMANEVAL_IDS)
    assert all(task.suite == "humaneval" for task in tasks)


def test_load_canary_reports_missing_anchor_task(tmp_path) -> None:
    cache = tmp_path / "benchmarks"
    # A valid 164-task cache whose IDs do not include the curated anchors.
    _write_humaneval_cache(cache, [f"HumanEval/{1000 + index}" for index in range(164)])

    with pytest.raises(TaskLoadError, match="canary references unknown HumanEval tasks"):
        load_suite("canary", cache_dir=cache)


_EVALPLUS_RECORD = {
    "task_id": "HumanEval/0",
    "entry_point": "is_even",
    "prompt": "",
    "canonical_solution": "def is_even(n):\n    return n % 2 == 0\n",
    "base_input": [[0], [2]],
    "plus_input": [[6], [7]],
    "atol": 0,
}


def _write_evalplus_cache(cache, records, filename="HumanEvalPlus.jsonl") -> None:
    cache.mkdir(exist_ok=True)
    (cache / filename).write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def test_build_evalplus_task_differential_scoring() -> None:
    from local_code_bench.scoring import score_completion
    from local_code_bench.tasks import build_evalplus_task

    task = build_evalplus_task(
        task_id="HumanEval/0",
        suite="humaneval-plus",
        prompt="",
        canonical_solution=_EVALPLUS_RECORD["canonical_solution"],
        entry_point="is_even",
        inputs=[[0], [2], [6], [7]],
    )

    correct = "def is_even(n):\n    return n % 2 == 0"
    # Right on the base inputs (0, 2) but wrong on a plus input (6).
    subtly_wrong = "def is_even(n):\n    return n in (0, 2)"

    assert score_completion(task, correct).passed is True
    assert score_completion(task, subtly_wrong).passed is False


def test_build_evalplus_task_float_tolerance() -> None:
    from local_code_bench.scoring import score_completion
    from local_code_bench.tasks import build_evalplus_task

    task = build_evalplus_task(
        task_id="HumanEval/float",
        suite="humaneval-plus",
        prompt="",
        canonical_solution="def half(x):\n    return x / 2\n",
        entry_point="half",
        inputs=[[1], [3]],
        atol=1e-6,
    )

    # Differs from the reference well within atol, so it must still pass.
    close_enough = "def half(x):\n    return x / 2 + 1e-9"
    assert score_completion(task, close_enough).passed is True


def test_load_evalplus_parses_cache_and_scores(tmp_path) -> None:
    from local_code_bench.scoring import score_completion

    cache = tmp_path / "benchmarks"
    _write_evalplus_cache(cache, [_EVALPLUS_RECORD])

    tasks = load_suite("humaneval-plus", cache_dir=cache)

    assert len(tasks) == 1
    assert tasks[0].task_id == "HumanEval/0"
    assert tasks[0].suite == "humaneval-plus"
    assert tasks[0].entry_point == "is_even"
    assert score_completion(tasks[0], "def is_even(n):\n    return n % 2 == 0").passed is True


def test_load_evalplus_reports_missing_file(tmp_path) -> None:
    cache = tmp_path / "benchmarks"
    cache.mkdir()

    with pytest.raises(TaskLoadError, match="requires a cached EvalPlus release file"):
        load_suite("humaneval-plus", cache_dir=cache)


def test_load_evalplus_reports_missing_canonical(tmp_path) -> None:
    cache = tmp_path / "benchmarks"
    bad = {**_EVALPLUS_RECORD}
    del bad["canonical_solution"]
    _write_evalplus_cache(cache, [bad])

    with pytest.raises(TaskLoadError, match="missing canonical_solution"):
        load_suite("humaneval-plus", cache_dir=cache)
