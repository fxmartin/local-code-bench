"""Endpoint benchmark orchestration."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# Coding-suite solutions are short; cap generation when a model config does not
# set its own max_tokens so verbose models cannot waste decode time and cost.
DEFAULT_ENDPOINT_MAX_TOKENS = 1024


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
    progress: Callable[[str], None] | None = None,
    max_tokens: int | None = None,
    concurrency_override: int | None = None,
) -> dict[str, int]:
    model_list = list(models)
    task_list = list(tasks)
    if not resume or not result_path.exists():
        append_jsonl(result_path, run_metadata(models=model_list, suite=_suite_name(task_list)))
    done = completed_pairs(result_path) if resume else set()
    summary = {"passed": 0, "failed": 0, "infra_failed": 0, "skipped": 0}
    total = len(model_list) * len(task_list)
    state = _ProgressState(current=0, total=total, progress=progress)
    for model in model_list:
        model_max_tokens = _resolve_max_tokens(model, max_tokens)
        try:
            provider = provider_for_model(model)
        except ProviderError as exc:
            for task in task_list:
                if _skip_done(model, task, done, summary, state):
                    continue
                record = failure_record(model, task, str(exc), infra=True)
                append_jsonl(result_path, record)
                summary["infra_failed"] += 1
                state.emit(model.name, task.task_id, "infra-failed")
            continue

        concurrency = _resolve_concurrency(model, concurrency_override)
        if concurrency <= 1:
            for task in task_list:
                if _skip_done(model, task, done, summary, state):
                    continue
                record, summary_key, status = _execute_task(provider, model, task, model_max_tokens)
                append_jsonl(result_path, record)
                summary[summary_key] += 1
                state.emit(model.name, task.task_id, status)
            continue

        pending = [task for task in task_list if not _skip_done(model, task, done, summary, state)]
        # Workers only run network + scoring (the slow part) in parallel; every
        # write, counter, and progress emit stays on this thread, so result
        # records and the summary need no extra locking.
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {
                pool.submit(_execute_task, provider, model, task, model_max_tokens): task
                for task in pending
            }
            for future in as_completed(futures):
                task = futures[future]
                record, summary_key, status = future.result()
                append_jsonl(result_path, record)
                summary[summary_key] += 1
                state.emit(model.name, task.task_id, status)
    return summary


def _execute_task(
    provider: object,
    model: ModelConfig,
    task: BenchmarkTask,
    max_tokens: int | None,
) -> tuple[dict[str, object], str, str]:
    try:
        measurement = capture_stream_metrics(
            provider.stream_chat(  # type: ignore[attr-defined]
                ChatRequest(prompt=task.prompt, temperature=0.0, max_tokens=max_tokens)
            ),
            task.prompt,
        )
        score = score_completion(task, measurement.response)
        record = endpoint_record(model, task, measurement, score.passed, score.reason)
        return record, ("passed" if score.passed else "failed"), ("passed" if score.passed else "failed")
    except ProviderError as exc:
        return failure_record(model, task, str(exc), infra=True), "infra_failed", "infra-failed"
    except Exception as exc:
        return failure_record(model, task, f"scoring failed: {exc}", infra=False), "failed", "failed"


def _skip_done(
    model: ModelConfig,
    task: BenchmarkTask,
    done: set[tuple[str, str]],
    summary: dict[str, int],
    state: _ProgressState,
) -> bool:
    if (model.name, task.task_id) in done:
        summary["skipped"] += 1
        state.emit(model.name, task.task_id, "skipped")
        return True
    return False


def _resolve_max_tokens(model: ModelConfig, override: int | None) -> int | None:
    if override is not None:
        return override
    if model.max_tokens is not None:
        return model.max_tokens
    return DEFAULT_ENDPOINT_MAX_TOKENS


def _resolve_concurrency(model: ModelConfig, override: int | None) -> int:
    if override is not None:
        return override
    return model.concurrency


class _ProgressState:
    """Sequential progress counter shared across a suite run."""

    def __init__(
        self,
        *,
        current: int,
        total: int,
        progress: Callable[[str], None] | None,
    ) -> None:
        self.current = current
        self.total = total
        self._progress = progress

    def emit(self, model_name: str, task_id: str, status: str) -> None:
        self.current += 1
        _emit_progress(self._progress, self.current, self.total, model_name, task_id, status)


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


def _emit_progress(
    progress: Callable[[str], None] | None,
    current: int,
    total: int,
    model_name: str,
    task_id: str,
    status: str,
) -> None:
    if progress is None:
        return
    progress(f"[{current}/{total}] {model_name} {task_id}: {status}")
