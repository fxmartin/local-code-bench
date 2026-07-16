"""Leaderboard generation from raw JSONL records."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import median

from local_code_bench.engine_provenance import backend_fingerprint, backend_label
from local_code_bench.results import read_jsonl


def generate_leaderboard(result_paths: list[Path], output_path: Path) -> str:
    records = _dedupe_latest([record for path in result_paths for record in read_jsonl(path)])
    endpoint = _endpoint_rows(records)
    agent = _agent_rows(records)
    lines = [
        "# LEADERBOARD",
        "",
        "Ranking: endpoint rows sort by pass@1 desc, then median latency asc, then cost asc.",
        "",
        "## Endpoint Models",
        "",
        "| Model | Engine | pass@1 | Median Latency | Prefill tok/s | Decode tok/s | $/task | Infra Failures |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(endpoint or ["| _No endpoint records_ | | | | | | | |"])
    lines.extend(
        [
            "",
            "## Agent Runs",
            "",
            "| Agent | Engine | pass@1 | Median Wall Time | Sandbox | Failures | Cost / Tokens |",
            "|---|---|---:|---:|---|---:|---|",
        ]
    )
    lines.extend(agent or ["| _No agent records_ | | | | | | |"])
    content = "\n".join(lines) + "\n"
    output_path.write_text(content, encoding="utf-8")
    return content


def _endpoint_rows(records: list[dict[str, object]]) -> list[str]:
    grouped: dict[tuple[str, object], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        if record.get("run_mode") == "endpoint" and isinstance(record.get("model"), str):
            grouped[
                (
                    str(record["model"]),
                    backend_fingerprint(
                        record.get("engine"), record.get("endpoint_provider")
                    ),
                )
            ].append(record)
    rows = []
    for (model, _engine), items in grouped.items():
        attempts = len(items)
        passed = sum(1 for item in items if item.get("passed") is True)
        latencies = [_metric(item, "latency_seconds") for item in items]
        prefill = [_metric(item, "prefill_tokens_per_second") for item in items]
        decode = [_metric(item, "decode_tokens_per_second") for item in items]
        costs = [float(item.get("cost_usd", 0.0) or 0.0) for item in items]
        infra = sum(1 for item in items if item.get("failure_type") == "infra")
        rows.append(
            (
                passed / attempts if attempts else 0.0,
                _median(latencies),
                sum(costs) / attempts if attempts else 0.0,
                f"| {model} | {backend_label(items[0].get('engine'), items[0].get('endpoint_provider'))} | {passed}/{attempts} | "
                f"{_fmt(_median(latencies))} | "
                f"{_fmt(_median(prefill))} | {_fmt(_median(decode))} | "
                f"{sum(costs) / attempts if attempts else 0.0:.6f} | {infra} |",
            )
        )
    return [row for *_sort, row in sorted(rows, key=lambda item: (-item[0], item[1], item[2]))]


def _agent_rows(records: list[dict[str, object]]) -> list[str]:
    grouped: dict[tuple[str, object], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        if record.get("run_mode") == "agent" and isinstance(record.get("agent"), str):
            grouped[
                (
                    str(record["agent"]),
                    backend_fingerprint(
                        record.get("engine"), record.get("endpoint_provider")
                    ),
                )
            ].append(record)
    rows = []
    for (agent, _engine), items in grouped.items():
        attempts = len(items)
        passed = sum(1 for item in items if item.get("passed") is True)
        walls = [float(item["wall_time_seconds"]) for item in items if isinstance(item.get("wall_time_seconds"), int | float)]
        sandbox = str(items[0].get("sandbox_mode", "unknown"))
        failures = attempts - passed
        rows.append(
            f"| {agent} | {backend_label(items[0].get('engine'), items[0].get('endpoint_provider'))} | {passed}/{attempts} | "
            f"{_fmt(_median(walls))} | {sandbox} | {failures} | "
            f"{_agent_cost_or_tokens(items)} |"
        )
    return rows


def _agent_cost_or_tokens(records: list[dict[str, object]]) -> str:
    totals = []
    for record in records:
        tokens = record.get("tokens")
        if isinstance(tokens, dict) and isinstance(tokens.get("total"), int | float):
            totals.append(float(tokens["total"]))
    if not totals:
        return "unavailable"
    return f"{_fmt_int(_median(totals))} tok"


def _metric(record: dict[str, object], key: str) -> float:
    metrics = record.get("metrics")
    if isinstance(metrics, dict) and isinstance(metrics.get(key), int | float):
        return float(metrics[key])
    return 0.0


def _median(values: list[float]) -> float:
    nonzero = [value for value in values if value > 0]
    return float(median(nonzero)) if nonzero else 0.0


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def _fmt_int(value: float) -> str:
    return f"{round(value):,}"


def _dedupe_latest(records: list[dict[str, object]]) -> list[dict[str, object]]:
    latest: dict[tuple[str, str, object, str], dict[str, object]] = {}
    passthrough: list[dict[str, object]] = []
    for record in records:
        run_mode = record.get("run_mode")
        if run_mode == "endpoint" and isinstance(record.get("model"), str) and isinstance(record.get("task_id"), str):
            latest[
                (
                    "endpoint",
                    str(record["model"]),
                    backend_fingerprint(
                        record.get("engine"), record.get("endpoint_provider")
                    ),
                    str(record["task_id"]),
                )
            ] = record
        elif run_mode == "agent" and isinstance(record.get("agent"), str) and isinstance(record.get("task_id"), str):
            latest[
                (
                    "agent",
                    str(record["agent"]),
                    backend_fingerprint(
                        record.get("engine"), record.get("endpoint_provider")
                    ),
                    str(record["task_id"]),
                )
            ] = record
        elif record.get("record_type") != "metadata":
            passthrough.append(record)
    return [*latest.values(), *passthrough]
