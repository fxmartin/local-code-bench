"""Endpoint benchmark orchestration."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

from local_code_bench.config import ModelConfig
from local_code_bench.cost import calculate_cost_usd
from local_code_bench.metadata import run_metadata
from local_code_bench.metrics import CompletionMeasurement, capture_stream_metrics
from local_code_bench.provider import ChatRequest, ProviderError, provider_for_model
from local_code_bench.results import append_jsonl, read_jsonl
from local_code_bench.scoring import score_completion
from local_code_bench.tasks import BenchmarkTask


def select_models(
    models: dict[str, ModelConfig],
    *,
    include: str | None = None,
    skip: str | None = None,
) -> list[ModelConfig]:
    selected = list(models.values())
    if include:
        requested = [name.strip() for name in include.split(",") if name.strip()]
        missing = [name for name in requested if name not in models]
        if missing:
            raise ValueError(f"unknown model(s): {', '.join(missing)}")
        selected = [models[name] for name in requested]
    if skip:
        skipped = {name.strip() for name in skip.split(",") if name.strip()}
        selected = [model for model in selected if model.name not in skipped]
    return selected


def completed_pairs(result_path: Path) -> set[tuple[str, str]]:
    if not result_path.exists():
        return set()
    pairs = set()
    for record in read_jsonl(result_path):
        if record.get("record_type") == "metadata":
            continue
        model = record.get("model")
        task_id = record.get("task_id")
        if isinstance(model, str) and isinstance(task_id, str):
            pairs.add((model, task_id))
    return pairs


def run_endpoint_suite(
    *,
    models: Iterable[ModelConfig],
    tasks: Iterable[BenchmarkTask],
    result_path: Path,
    resume: bool = False,
) -> dict[str, int]:
    model_list = list(models)
    task_list = list(tasks)
    if not resume or not result_path.exists():
        append_jsonl(result_path, run_metadata(models=model_list, suite=_suite_name(task_list)))
    done = completed_pairs(result_path) if resume else set()
    summary = {"passed": 0, "failed": 0, "infra_failed": 0, "skipped": 0}
    for model in model_list:
        try:
            provider = provider_for_model(model)
        except ProviderError as exc:
            for task in task_list:
                if (model.name, task.task_id) in done:
                    summary["skipped"] += 1
                    continue
                append_jsonl(result_path, failure_record(model, task, str(exc), infra=True))
                summary["infra_failed"] += 1
            continue
        for task in task_list:
            if (model.name, task.task_id) in done:
                summary["skipped"] += 1
                continue
            try:
                measurement = capture_stream_metrics(
                    provider.stream_chat(ChatRequest(prompt=task.prompt, temperature=0.0)),
                    task.prompt,
                )
                score = score_completion(task, measurement.response)
                record = endpoint_record(model, task, measurement, score.passed, score.reason)
                summary["passed" if score.passed else "failed"] += 1
            except ProviderError as exc:
                record = failure_record(model, task, str(exc), infra=True)
                summary["infra_failed"] += 1
            except Exception as exc:
                record = failure_record(model, task, f"scoring failed: {exc}", infra=False)
                summary["failed"] += 1
            append_jsonl(result_path, record)
    return summary


def endpoint_record(
    model: ModelConfig,
    task: BenchmarkTask,
    measurement: CompletionMeasurement,
    passed: bool | None,
    reason: str | None,
) -> dict[str, object]:
    return {
        "run_mode": "endpoint",
        "model": model.name,
        "provider_type": model.type,
        "model_id": model.model_id,
        "pinned_revision": model.pinned_revision,
        "task_id": task.task_id,
        "suite": task.suite,
        "suite_version": task.version,
        "prompt": task.prompt,
        "raw_response": measurement.response,
        "passed": passed,
        "failure_reason": reason,
        "cost_usd": calculate_cost_usd(
            model,
            measurement.prompt_tokens,
            measurement.completion_tokens,
        ),
        "metrics": {
            "ttft_seconds": measurement.ttft_seconds,
            "latency_seconds": measurement.latency_seconds,
            "prefill_tokens_per_second": measurement.prefill_tokens_per_second,
            "decode_tokens_per_second": measurement.decode_tokens_per_second,
        },
        "tokens": {
            "prompt": measurement.prompt_tokens,
            "completion": measurement.completion_tokens,
            "estimated": measurement.token_counts_estimated,
        },
    }


def failure_record(
    model: ModelConfig,
    task: BenchmarkTask,
    reason: str,
    *,
    infra: bool,
) -> dict[str, object]:
    return {
        "run_mode": "endpoint",
        "model": model.name,
        "provider_type": model.type,
        "model_id": model.model_id,
        "task_id": task.task_id,
        "suite": task.suite,
        "passed": False,
        "failure_reason": reason,
        "failure_type": "infra" if infra else "model",
        "cost_usd": 0.0,
    }


def task_to_dict(task: BenchmarkTask) -> dict[str, object]:
    return asdict(task)


def _suite_name(tasks: list[BenchmarkTask]) -> str | None:
    suites = sorted({task.suite for task in tasks})
    return ",".join(suites) if suites else None
