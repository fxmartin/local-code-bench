"""Offline re-scoring of stored endpoint results."""

from __future__ import annotations

from pathlib import Path

from local_code_bench.results import append_jsonl, read_jsonl
from local_code_bench.scoring import score_completion
from local_code_bench.tasks import BenchmarkTask


def rescore_endpoint_records(
    *,
    input_path: Path,
    output_path: Path,
    tasks: list[BenchmarkTask],
) -> dict[str, int]:
    task_by_id = {task.task_id: task for task in tasks}
    summary = {"rescored": 0, "missing_task": 0}
    for record in read_jsonl(input_path):
        if record.get("run_mode") != "endpoint" or record.get("record_type") == "metadata":
            append_jsonl(output_path, record)
            continue
        task_id = record.get("task_id")
        raw_response = record.get("raw_response")
        if not isinstance(task_id, str) or task_id not in task_by_id or not isinstance(raw_response, str):
            updated = dict(record)
            updated["passed"] = False
            updated["failure_reason"] = "offline re-score missing task or raw response"
            append_jsonl(output_path, updated)
            summary["missing_task"] += 1
            continue
        score = score_completion(task_by_id[task_id], raw_response)
        updated = dict(record)
        updated["passed"] = score.passed
        updated["failure_reason"] = score.reason
        updated["rescored_offline"] = True
        append_jsonl(output_path, updated)
        summary["rescored"] += 1
    return summary
