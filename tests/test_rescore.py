from __future__ import annotations

from local_code_bench.rescore import rescore_endpoint_records
from local_code_bench.results import append_jsonl, read_jsonl
from local_code_bench.tasks import BenchmarkTask


def test_rescore_endpoint_records_recomputes_pass_fail(tmp_path) -> None:
    input_path = tmp_path / "run.jsonl"
    output_path = tmp_path / "rescored.jsonl"
    task = BenchmarkTask(
        task_id="t1",
        suite="humaneval",
        prompt="",
        test_code="assert add(1, 2) == 3",
        entry_point="add",
        version="fixture",
    )
    append_jsonl(
        input_path,
        {
            "run_mode": "endpoint",
            "model": "m",
            "task_id": "t1",
            "raw_response": "def add(a, b):\n    return a + b",
            "passed": False,
        },
    )

    summary = rescore_endpoint_records(input_path=input_path, output_path=output_path, tasks=[task])

    records = read_jsonl(output_path)
    assert summary == {"rescored": 1, "missing_task": 0}
    assert records[0]["passed"] is True
    assert records[0]["rescored_offline"] is True
