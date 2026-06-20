from __future__ import annotations

import gzip
import json

from local_code_bench.tasks import load_humaneval, load_mbpp


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
