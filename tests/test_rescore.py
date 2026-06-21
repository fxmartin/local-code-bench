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


def test_rescore_endpoint_records_preserves_non_endpoint_records(tmp_path) -> None:
    input_path = tmp_path / "run.jsonl"
    output_path = tmp_path / "rescored.jsonl"
    append_jsonl(input_path, {"record_type": "metadata", "suite": "humaneval"})
    append_jsonl(input_path, {"run_mode": "agent", "agent": "codex", "task_id": "t1"})

    summary = rescore_endpoint_records(input_path=input_path, output_path=output_path, tasks=[])

    assert summary == {"rescored": 0, "missing_task": 0}
    assert read_jsonl(output_path) == [
        {"record_type": "metadata", "suite": "humaneval"},
        {"run_mode": "agent", "agent": "codex", "task_id": "t1"},
    ]


def test_rescore_endpoint_records_marks_missing_task_or_raw_response(tmp_path) -> None:
    input_path = tmp_path / "run.jsonl"
    output_path = tmp_path / "rescored.jsonl"
    append_jsonl(
        input_path,
        {
            "run_mode": "endpoint",
            "model": "m",
            "task_id": "unknown",
            "raw_response": "def add(a, b):\n    return a + b",
            "passed": True,
        },
    )
    append_jsonl(
        input_path,
        {
            "run_mode": "endpoint",
            "model": "m",
            "task_id": "t1",
            "passed": True,
        },
    )
    task = BenchmarkTask("t1", "humaneval", "", "assert add(1, 2) == 3", "add", "fixture")

    summary = rescore_endpoint_records(input_path=input_path, output_path=output_path, tasks=[task])

    records = read_jsonl(output_path)
    assert summary == {"rescored": 0, "missing_task": 2}
    assert records[0]["passed"] is False
    assert records[1]["failure_reason"] == "offline re-score missing task or raw response"
