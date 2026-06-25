"""Tests for the dashboard result aggregation model (Story 07.1-001)."""

from __future__ import annotations

from local_code_bench.dashboard_model import (
    build_dashboard_data,
    load_dashboard_data,
)
from local_code_bench.results import append_jsonl


def _endpoint_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "run_mode": "endpoint",
        "model": "m1",
        "suite": "humaneval",
        "task_id": "HumanEval/0",
        "passed": True,
        "failure_reason": None,
        "cost_usd": 0.01,
        "raw_response": "def solution():\n    return 1\n",
        "metrics": {
            "ttft_seconds": 0.2,
            "latency_seconds": 1.0,
            "prefill_tokens_per_second": 100.0,
            "decode_tokens_per_second": 50.0,
        },
        "tokens": {"prompt": 30, "completion": 10, "estimated": False},
    }
    record.update(overrides)
    return record


def test_endpoint_records_grouped_with_aggregated_metrics() -> None:
    records = [
        _endpoint_record(task_id="HumanEval/0", passed=True),
        _endpoint_record(
            task_id="HumanEval/1",
            passed=False,
            failure_reason="wrong answer",
            failure_type="model",
            cost_usd=0.02,
            metrics={
                "ttft_seconds": 0.4,
                "latency_seconds": 3.0,
                "prefill_tokens_per_second": 80.0,
                "decode_tokens_per_second": 40.0,
            },
            tokens={"prompt": 50, "completion": 20, "estimated": False},
        ),
    ]

    data = build_dashboard_data(records)

    assert len(data.endpoint_models) == 1
    agg = data.endpoint_models[0]
    assert agg.model == "m1"
    assert agg.suite == "humaneval"
    assert agg.run_mode == "endpoint"
    assert agg.attempts == 2
    assert agg.passed == 1
    assert agg.pass_rate == 0.5
    assert agg.failure_count == 1
    assert agg.model_failures == 1
    # Median across the two attempts (1.0, 3.0) -> 2.0.
    assert agg.median_latency_seconds == 2.0
    assert agg.median_ttft_seconds == 0.30000000000000004
    assert agg.median_prefill_tokens_per_second == 90.0
    assert agg.median_decode_tokens_per_second == 45.0
    assert agg.total_prompt_tokens == 80
    assert agg.total_completion_tokens == 30
    assert agg.total_cost_usd == 0.03
    assert agg.mean_cost_usd == 0.015
    # Per-task drilldown is available to consumers.
    assert {task.task_id for task in agg.tasks} == {"HumanEval/0", "HumanEval/1"}
    failing = next(task for task in agg.tasks if task.task_id == "HumanEval/1")
    assert failing.passed is False
    assert failing.failure_reason == "wrong answer"
    assert failing.cost_usd == 0.02


def test_endpoint_groups_split_by_model_and_suite() -> None:
    records = [
        _endpoint_record(model="m1", suite="humaneval"),
        _endpoint_record(model="m1", suite="mbpp", task_id="Mbpp/1"),
        _endpoint_record(model="m2", suite="humaneval", task_id="HumanEval/2"),
    ]

    data = build_dashboard_data(records)

    keys = {(agg.model, agg.suite) for agg in data.endpoint_models}
    assert keys == {("m1", "humaneval"), ("m1", "mbpp"), ("m2", "humaneval")}


def test_infra_failures_counted_separately() -> None:
    records = [
        _endpoint_record(task_id="HumanEval/0", passed=True),
        _endpoint_record(
            task_id="HumanEval/9",
            passed=False,
            failure_reason="timeout",
            failure_type="infra",
            cost_usd=0.0,
        ),
    ]

    agg = build_dashboard_data(records).endpoint_models[0]

    assert agg.infra_failures == 1
    assert agg.model_failures == 0
    assert agg.failure_count == 1


def test_endpoint_dedupes_to_latest_attempt_per_task() -> None:
    records = [
        _endpoint_record(task_id="HumanEval/0", passed=False, cost_usd=0.05),
        _endpoint_record(task_id="HumanEval/0", passed=True, cost_usd=0.01),
    ]

    agg = build_dashboard_data(records).endpoint_models[0]

    assert agg.attempts == 1
    assert agg.passed == 1
    assert agg.total_cost_usd == 0.01


def test_agent_rows_expose_wall_time_without_throughput_mixing() -> None:
    records = [
        {
            "run_mode": "agent",
            "agent": "codex",
            "suite": "humaneval",
            "task_id": "HumanEval/0",
            "passed": True,
            "failure_reason": None,
            "wall_time_seconds": 7.0,
            "sandbox_mode": "workspace-write",
            "exit_code": 0,
            "cost_status": "tokens_available",
        },
        {
            "run_mode": "agent",
            "agent": "codex",
            "suite": "humaneval",
            "task_id": "HumanEval/1",
            "passed": False,
            "failure_reason": "codex timeout",
            "wall_time_seconds": 5.0,
            "sandbox_mode": "workspace-write",
            "exit_code": None,
            "cost_status": "unavailable",
        },
    ]

    data = build_dashboard_data(records)

    assert len(data.agent_runs) == 1
    agg = data.agent_runs[0]
    assert agg.agent == "codex"
    assert agg.run_mode == "agent"
    assert agg.pass_rate == 0.5
    assert agg.median_wall_time_seconds == 6.0
    assert agg.sandbox_mode == "workspace-write"
    failing = next(task for task in agg.tasks if task.task_id == "HumanEval/1")
    assert failing.failure_reason == "codex timeout"
    assert failing.exit_code is None
    # Agent aggregates carry no endpoint throughput fields.
    assert not hasattr(agg, "median_prefill_tokens_per_second")


def test_sweep_points_exposed_for_charting() -> None:
    records = [
        {
            "run_mode": "sweep",
            "model": "m1",
            "context_tokens": 8000,
            "metrics": {"ttft_seconds": 1.5, "prefill_tokens_per_second": 200.0},
        },
        {
            "run_mode": "sweep",
            "model": "m1",
            "context_tokens": 2000,
            "metrics": {"ttft_seconds": 0.5, "prefill_tokens_per_second": 300.0},
        },
    ]

    data = build_dashboard_data(records)

    assert len(data.sweep_points) == 2
    # Sorted by model then context size for stable charting.
    first = data.sweep_points[0]
    assert (first.model, first.context_tokens) == ("m1", 2000)
    assert first.ttft_seconds == 0.5
    assert first.prefill_tokens_per_second == 300.0


def test_sweep_dedupes_latest_per_model_and_context() -> None:
    records = [
        {
            "run_mode": "sweep",
            "model": "m1",
            "context_tokens": 2000,
            "metrics": {"ttft_seconds": 9.0, "prefill_tokens_per_second": 10.0},
        },
        {
            "run_mode": "sweep",
            "model": "m1",
            "context_tokens": 2000,
            "metrics": {"ttft_seconds": 0.5, "prefill_tokens_per_second": 300.0},
        },
    ]

    data = build_dashboard_data(records)

    assert len(data.sweep_points) == 1
    assert data.sweep_points[0].ttft_seconds == 0.5


def test_malformed_records_become_warnings_not_crashes() -> None:
    records = [
        _endpoint_record(task_id="HumanEval/0", passed=True),
        {"run_mode": "endpoint"},  # missing model/task_id
        {"run_mode": "endpoint", "model": "m1"},  # missing task_id
        "not-a-dict",  # wrong type
    ]

    data = build_dashboard_data(records)

    # Valid record still aggregated.
    assert len(data.endpoint_models) == 1
    assert data.endpoint_models[0].attempts == 1
    # Three problem records produce warnings.
    assert len(data.warnings) == 3
    assert all(warning.message for warning in data.warnings)


def test_metadata_and_power_records_are_ignored_without_warnings() -> None:
    records = [
        _endpoint_record(task_id="HumanEval/0", passed=True),
        {"record_type": "metadata", "suite": "humaneval"},
        {"record_type": "power", "model": "m1", "available": True},
    ]

    data = build_dashboard_data(records)

    assert len(data.endpoint_models) == 1
    assert data.warnings == ()


def test_load_dashboard_data_reads_files_and_reports_bad_lines(tmp_path) -> None:
    good = tmp_path / "run.jsonl"
    append_jsonl(good, _endpoint_record(task_id="HumanEval/0", passed=True))
    append_jsonl(good, _endpoint_record(task_id="HumanEval/1", passed=False))
    # Append a syntactically broken JSONL line directly.
    with good.open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")

    data = load_dashboard_data([good])

    assert data.endpoint_models[0].attempts == 2
    assert len(data.warnings) == 1
    warning = data.warnings[0]
    assert warning.line == 3
    assert str(good) in warning.source


def test_load_dashboard_data_handles_missing_file(tmp_path) -> None:
    data = load_dashboard_data([tmp_path / "absent.jsonl"])

    assert data.endpoint_models == ()
    assert data.agent_runs == ()
    assert data.sweep_points == ()
    assert data.warnings == ()


def test_raw_response_preview_is_bounded() -> None:
    record = _endpoint_record(task_id="HumanEval/0", raw_response="x" * 5000)

    agg = build_dashboard_data([record]).endpoint_models[0]

    preview = agg.tasks[0].raw_response_preview
    assert len(preview) <= 512


def test_unrecognized_run_mode_becomes_warning() -> None:
    records = [
        _endpoint_record(task_id="HumanEval/0", passed=True),
        {"run_mode": "mystery", "model": "m1", "task_id": "HumanEval/3"},
    ]

    data = build_dashboard_data(records)

    assert len(data.endpoint_models) == 1
    assert len(data.warnings) == 1
    assert "unrecognized run_mode" in data.warnings[0].message


def test_sweep_record_missing_fields_becomes_warning() -> None:
    records = [
        {"run_mode": "sweep", "model": "m1"},  # missing context_tokens
        {"run_mode": "sweep", "context_tokens": 2000},  # missing model
        # context_tokens given as bool must not be accepted as an int.
        {"run_mode": "sweep", "model": "m1", "context_tokens": True},
    ]

    data = build_dashboard_data(records)

    assert data.sweep_points == ()
    assert len(data.warnings) == 3
    assert all("sweep record missing" in warning.message for warning in data.warnings)


def test_missing_or_non_dict_metrics_yield_no_throughput() -> None:
    records = [
        _endpoint_record(task_id="HumanEval/0", metrics=None),
        _endpoint_record(task_id="HumanEval/1", metrics="not-a-dict"),
    ]

    agg = build_dashboard_data(records).endpoint_models[0]

    assert agg.median_ttft_seconds is None
    assert agg.median_latency_seconds is None
    assert agg.median_prefill_tokens_per_second is None
    assert agg.median_decode_tokens_per_second is None
    assert all(task.ttft_seconds is None for task in agg.tasks)


def test_token_counts_handle_float_and_invalid_values() -> None:
    records = [
        # Float token counts are truncated to ints.
        _endpoint_record(task_id="HumanEval/0", tokens={"prompt": 12.9, "completion": 4.1}),
        # Non-numeric / missing token entries contribute zero.
        _endpoint_record(task_id="HumanEval/1", tokens={"prompt": "oops"}),
        _endpoint_record(task_id="HumanEval/2", tokens="not-a-dict"),
    ]

    agg = build_dashboard_data(records).endpoint_models[0]

    # 12 (truncated) + 0 + 0; completion 4 + 0 + 0.
    assert agg.total_prompt_tokens == 12
    assert agg.total_completion_tokens == 4


def test_non_numeric_and_boolean_cost_is_treated_as_zero() -> None:
    records = [
        _endpoint_record(task_id="HumanEval/0", cost_usd="free"),
        _endpoint_record(task_id="HumanEval/1", cost_usd=True),
    ]

    agg = build_dashboard_data(records).endpoint_models[0]

    assert agg.total_cost_usd == 0.0
    assert agg.mean_cost_usd == 0.0
    assert all(task.cost_usd == 0.0 for task in agg.tasks)


def test_non_string_raw_response_previews_to_empty() -> None:
    record = _endpoint_record(task_id="HumanEval/0", raw_response={"unexpected": "shape"})

    agg = build_dashboard_data([record]).endpoint_models[0]

    assert agg.tasks[0].raw_response_preview == ""


def test_load_dashboard_data_skips_blank_lines_and_flags_non_object_lines(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    append_jsonl(path, _endpoint_record(task_id="HumanEval/0", passed=True))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")  # blank line is skipped silently
        handle.write("   \n")  # whitespace-only line is skipped too
        handle.write("[1, 2, 3]\n")  # valid JSON but not an object

    data = load_dashboard_data([path])

    assert data.endpoint_models[0].attempts == 1
    assert len(data.warnings) == 1
    warning = data.warnings[0]
    assert "not a JSON object" in warning.message
    assert warning.line == 4
