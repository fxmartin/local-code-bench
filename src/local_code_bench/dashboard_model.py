"""Dashboard result aggregation model.

Builds dashboard-ready aggregates from endpoint, agent, and sweep JSONL files so
that every dashboard view (static HTML artifact, live local server) consumes one
consistent interpretation of the benchmark data.

This is a pure Python transform: :func:`build_dashboard_data` turns a list of raw
records into a :class:`DashboardData` structure, and :func:`load_dashboard_data`
reads JSONL files tolerantly, reporting unreadable lines as data-quality warnings
instead of crashing the whole dashboard.

Endpoint token-throughput metrics and agent wall-clock metrics are kept in
separate aggregate types so agent runs never contaminate endpoint throughput.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median

RAW_RESPONSE_PREVIEW_LIMIT = 512


@dataclass(frozen=True)
class DataQualityWarning:
    """A record or JSONL line that could not be interpreted."""

    source: str
    message: str
    line: int | None = None


@dataclass(frozen=True)
class EndpointTaskResult:
    """Per-task endpoint detail for drilldown views."""

    task_id: str
    passed: bool | None
    failure_reason: str | None
    failure_type: str | None
    latency_seconds: float | None
    ttft_seconds: float | None
    prefill_tokens_per_second: float | None
    decode_tokens_per_second: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float
    raw_response_preview: str


@dataclass(frozen=True)
class EndpointModelAggregate:
    """Endpoint results grouped by model and suite."""

    model: str
    suite: str | None
    run_mode: str
    attempts: int
    passed: int
    pass_rate: float
    failure_count: int
    infra_failures: int
    model_failures: int
    median_latency_seconds: float | None
    median_ttft_seconds: float | None
    median_prefill_tokens_per_second: float | None
    median_decode_tokens_per_second: float | None
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cost_usd: float
    mean_cost_usd: float
    tasks: tuple[EndpointTaskResult, ...]


@dataclass(frozen=True)
class AgentTaskResult:
    """Per-task agent detail for drilldown views."""

    task_id: str
    passed: bool | None
    failure_reason: str | None
    wall_time_seconds: float | None
    exit_code: int | None
    cost_status: str | None


@dataclass(frozen=True)
class AgentAggregate:
    """Agent results grouped by agent and suite.

    Intentionally carries no token-throughput fields: agent wall-clock metrics
    must not be mixed into endpoint prefill/decode throughput.
    """

    agent: str
    suite: str | None
    run_mode: str
    attempts: int
    passed: int
    pass_rate: float
    failure_count: int
    median_wall_time_seconds: float | None
    sandbox_mode: str | None
    tasks: tuple[AgentTaskResult, ...]


@dataclass(frozen=True)
class SweepPoint:
    """A single context-size sweep observation for charting."""

    model: str
    context_tokens: int
    ttft_seconds: float | None
    prefill_tokens_per_second: float | None


@dataclass(frozen=True)
class DashboardData:
    """Aggregated, dashboard-ready view over a set of result records."""

    endpoint_models: tuple[EndpointModelAggregate, ...] = ()
    agent_runs: tuple[AgentAggregate, ...] = ()
    sweep_points: tuple[SweepPoint, ...] = ()
    warnings: tuple[DataQualityWarning, ...] = ()


def load_dashboard_data(paths: list[Path]) -> DashboardData:
    """Read result JSONL files tolerantly and build dashboard aggregates.

    Unreadable lines (invalid JSON or non-object payloads) are reported as
    data-quality warnings; readable records are aggregated normally.
    """

    records: list[dict[str, object]] = []
    warnings: list[DataQualityWarning] = []
    for path in paths:
        file_path = Path(path)
        if not file_path.exists():
            continue
        with file_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError as exc:
                    warnings.append(
                        DataQualityWarning(
                            source=str(file_path),
                            message=f"invalid JSON: {exc.msg}",
                            line=line_number,
                        )
                    )
                    continue
                if not isinstance(parsed, dict):
                    warnings.append(
                        DataQualityWarning(
                            source=str(file_path),
                            message="JSONL line is not a JSON object",
                            line=line_number,
                        )
                    )
                    continue
                records.append(parsed)
    return build_dashboard_data(records, warnings=warnings)


def build_dashboard_data(
    records: Sequence[object],
    *,
    warnings: list[DataQualityWarning] | None = None,
) -> DashboardData:
    """Transform raw result records into dashboard-ready aggregates."""

    collected: list[DataQualityWarning] = list(warnings or [])
    endpoint: dict[tuple[str, str | None], dict[str, dict[str, object]]] = defaultdict(dict)
    agent: dict[tuple[str, str | None], dict[str, dict[str, object]]] = defaultdict(dict)
    sweep: dict[tuple[str, int], dict[str, object]] = {}

    for record in records:
        if not isinstance(record, dict):
            collected.append(
                DataQualityWarning(source="record", message="record is not a JSON object")
            )
            continue
        if record.get("record_type") in {"metadata", "power"}:
            continue
        run_mode = record.get("run_mode")
        if run_mode == "endpoint":
            _ingest_keyed(record, "model", endpoint, collected)
        elif run_mode == "agent":
            _ingest_keyed(record, "agent", agent, collected)
        elif run_mode == "sweep":
            _ingest_sweep(record, sweep, collected)
        else:
            collected.append(
                DataQualityWarning(
                    source="record",
                    message=f"unrecognized run_mode: {run_mode!r}",
                )
            )

    endpoint_models = tuple(
        _endpoint_aggregate(model, suite, tasks.values())
        for (model, suite), tasks in sorted(endpoint.items(), key=_group_key)
    )
    agent_runs = tuple(
        _agent_aggregate(name, suite, tasks.values())
        for (name, suite), tasks in sorted(agent.items(), key=_group_key)
    )
    sweep_points = tuple(
        _sweep_point(record) for _, record in sorted(sweep.items(), key=lambda item: item[0])
    )
    return DashboardData(
        endpoint_models=endpoint_models,
        agent_runs=agent_runs,
        sweep_points=sweep_points,
        warnings=tuple(collected),
    )


def _ingest_keyed(
    record: dict[str, object],
    actor_key: str,
    target: dict[tuple[str, str | None], dict[str, dict[str, object]]],
    warnings: list[DataQualityWarning],
) -> None:
    actor = record.get(actor_key)
    task_id = record.get("task_id")
    if not isinstance(actor, str) or not isinstance(task_id, str):
        warnings.append(
            DataQualityWarning(
                source="record",
                message=f"{record.get('run_mode')} record missing {actor_key}/task_id",
            )
        )
        return
    suite = record.get("suite")
    suite_value = suite if isinstance(suite, str) else None
    # Latest record per (actor, suite, task_id) wins, matching leaderboard dedupe.
    target[(actor, suite_value)][task_id] = record


def _ingest_sweep(
    record: dict[str, object],
    target: dict[tuple[str, int], dict[str, object]],
    warnings: list[DataQualityWarning],
) -> None:
    model = record.get("model")
    context = record.get("context_tokens")
    if not isinstance(model, str) or not isinstance(context, int) or isinstance(context, bool):
        warnings.append(
            DataQualityWarning(
                source="record",
                message="sweep record missing model/context_tokens",
            )
        )
        return
    target[(model, context)] = record


def _endpoint_aggregate(
    model: str,
    suite: str | None,
    records: Iterable[dict[str, object]],
) -> EndpointModelAggregate:
    items = list(records)
    attempts = len(items)
    passed = sum(1 for item in items if item.get("passed") is True)
    infra = sum(1 for item in items if item.get("failure_type") == "infra")
    model_failures = sum(1 for item in items if item.get("failure_type") == "model")
    costs = [_as_float(item.get("cost_usd")) or 0.0 for item in items]
    total_cost = sum(costs)
    prompt_tokens = sum(_token_count(item, "prompt") for item in items)
    completion_tokens = sum(_token_count(item, "completion") for item in items)
    tasks = tuple(_endpoint_task(item) for item in items)
    return EndpointModelAggregate(
        model=model,
        suite=suite,
        run_mode="endpoint",
        attempts=attempts,
        passed=passed,
        pass_rate=passed / attempts if attempts else 0.0,
        failure_count=attempts - passed,
        infra_failures=infra,
        model_failures=model_failures,
        median_latency_seconds=_median([_metric(item, "latency_seconds") for item in items]),
        median_ttft_seconds=_median([_metric(item, "ttft_seconds") for item in items]),
        median_prefill_tokens_per_second=_median(
            [_metric(item, "prefill_tokens_per_second") for item in items]
        ),
        median_decode_tokens_per_second=_median(
            [_metric(item, "decode_tokens_per_second") for item in items]
        ),
        total_prompt_tokens=prompt_tokens,
        total_completion_tokens=completion_tokens,
        total_cost_usd=total_cost,
        mean_cost_usd=total_cost / attempts if attempts else 0.0,
        tasks=tuple(sorted(tasks, key=lambda task: task.task_id)),
    )


def _endpoint_task(record: dict[str, object]) -> EndpointTaskResult:
    return EndpointTaskResult(
        task_id=str(record.get("task_id")),
        passed=_as_bool(record.get("passed")),
        failure_reason=_as_str(record.get("failure_reason")),
        failure_type=_as_str(record.get("failure_type")),
        latency_seconds=_metric(record, "latency_seconds"),
        ttft_seconds=_metric(record, "ttft_seconds"),
        prefill_tokens_per_second=_metric(record, "prefill_tokens_per_second"),
        decode_tokens_per_second=_metric(record, "decode_tokens_per_second"),
        prompt_tokens=_token_count(record, "prompt") or None,
        completion_tokens=_token_count(record, "completion") or None,
        cost_usd=_as_float(record.get("cost_usd")) or 0.0,
        raw_response_preview=_preview(record.get("raw_response")),
    )


def _agent_aggregate(
    name: str,
    suite: str | None,
    records: Iterable[dict[str, object]],
) -> AgentAggregate:
    items = list(records)
    attempts = len(items)
    passed = sum(1 for item in items if item.get("passed") is True)
    walls = [_as_float(item.get("wall_time_seconds")) for item in items]
    sandbox = next(
        (str(item["sandbox_mode"]) for item in items if isinstance(item.get("sandbox_mode"), str)),
        None,
    )
    tasks = tuple(_agent_task(item) for item in items)
    return AgentAggregate(
        agent=name,
        suite=suite,
        run_mode="agent",
        attempts=attempts,
        passed=passed,
        pass_rate=passed / attempts if attempts else 0.0,
        failure_count=attempts - passed,
        median_wall_time_seconds=_median([value for value in walls if value is not None]),
        sandbox_mode=sandbox,
        tasks=tuple(sorted(tasks, key=lambda task: task.task_id)),
    )


def _agent_task(record: dict[str, object]) -> AgentTaskResult:
    exit_code = record.get("exit_code")
    return AgentTaskResult(
        task_id=str(record.get("task_id")),
        passed=_as_bool(record.get("passed")),
        failure_reason=_as_str(record.get("failure_reason")),
        wall_time_seconds=_as_float(record.get("wall_time_seconds")),
        exit_code=exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else None,
        cost_status=_as_str(record.get("cost_status")),
    )


def _sweep_point(record: dict[str, object]) -> SweepPoint:
    context = record.get("context_tokens")
    return SweepPoint(
        model=str(record.get("model")),
        context_tokens=context if isinstance(context, int) else 0,  # validated during ingest
        ttft_seconds=_metric(record, "ttft_seconds"),
        prefill_tokens_per_second=_metric(record, "prefill_tokens_per_second"),
    )


def _metric(record: dict[str, object], key: str) -> float | None:
    metrics = record.get("metrics")
    if isinstance(metrics, dict):
        return _as_float(metrics.get(key))
    return None


def _token_count(record: dict[str, object], key: str) -> int:
    tokens = record.get("tokens")
    if isinstance(tokens, dict):
        value = tokens.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, float):
            return int(value)
    return 0


def _median(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return float(median(present)) if present else None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _preview(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value[:RAW_RESPONSE_PREVIEW_LIMIT]


def _group_key(item: tuple[tuple[str, str | None], object]) -> tuple[str, str]:
    (actor, suite), _ = item
    return (actor, suite or "")
