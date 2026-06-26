"""Fixed-prompt invocation and capture for the OpenCode benchmark (Story 10.1-001).

A single entry point reads the version-controlled task prompts, sends each to a
chosen local model under identical, deterministic conditions, and captures the raw
response plus timing/token metrics into a JSONL run file. Every run logs the
provenance variables the article surfaced — quant string, quant provider
(Unsloth-vs-Bartowski), engine, endpoint, mode, and seed/temperature.

Reuses `provider_for_model` (OpenAI- or Anthropic-compatible streaming, so oMLX's
Anthropic endpoint works by setting the model's `type`), `capture_stream_metrics`,
and `ChatRequest`. CLI overrides are applied with `dataclasses.replace` so the
shared `ModelConfig` and existing flows are untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from local_code_bench.config import ConfigError, ModelConfig
from local_code_bench.metrics import CompletionMeasurement, capture_stream_metrics
from local_code_bench.opencode.engines import endpoint_for_engine
from local_code_bench.provider import ChatRequest, provider_for_model
from local_code_bench.results import append_jsonl, new_run_path

#: The two prompt files driven on every run (read from disk, never inlined).
OPENCODE_TASKS: tuple[str, ...] = ("task-a", "task-b")

RunMode = Literal["default", "thinking"]

#: Logged for reproducibility; temperature is pinned to 0 by default.
DEFAULT_SEED = 0
DEFAULT_TEMPERATURE = 0.0


@dataclass(frozen=True)
class OpenCodeOverrides:
    """Optional CLI overrides applied on top of a configured model."""

    endpoint: str | None = None
    engine: str | None = None
    quant: str | None = None
    provider: str | None = None


def resolve_model(
    model: ModelConfig,
    overrides: OpenCodeOverrides,
    *,
    mode: RunMode,
) -> ModelConfig:
    """Apply CLI overrides and the run mode to produce the effective model config.

    Endpoint precedence: an explicit ``--endpoint`` wins, else ``--engine`` maps to
    its default ``/v1`` URL, else the configured ``base_url`` is kept (a model's
    declared ``engine`` is provenance only and does not remap the URL). In
    ``thinking`` mode the model's ``thinking_extra_body`` is merged over its base
    ``extra_body`` so config can flip a model into its capable, high-reasoning path.
    """

    engine = overrides.engine or model.engine
    quant = overrides.quant or model.quant
    provider = overrides.provider or model.provider

    if overrides.endpoint is not None:
        base_url = overrides.endpoint.rstrip("/")
    elif overrides.engine is not None:
        base_url = endpoint_for_engine(overrides.engine)
    else:
        base_url = model.base_url

    return replace(
        model,
        base_url=base_url,
        engine=engine,
        quant=quant,
        provider=provider,
        extra_body=_effective_extra_body(model, mode),
    )


def _effective_extra_body(model: ModelConfig, mode: RunMode) -> dict[str, Any] | None:
    if mode != "thinking":
        return model.extra_body
    merged: dict[str, Any] = dict(model.extra_body or {})
    merged.update(model.thinking_extra_body or {})
    return merged or None


def load_prompt(prompts_dir: str | Path, task: str) -> tuple[Path, str]:
    """Read a task prompt from ``<prompts_dir>/<task>.md`` (version-controlled)."""

    path = Path(prompts_dir) / f"{task}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"prompt file not found: {path}") from exc
    if not text.strip():
        raise ConfigError(f"prompt file is empty: {path}")
    return path, text


def build_record(
    *,
    task: str,
    model: ModelConfig,
    mode: RunMode,
    seed: int,
    temperature: float,
    prompt_file: Path,
    measurement: CompletionMeasurement,
) -> dict[str, Any]:
    """Assemble one JSONL record: provenance metadata + captured metrics."""

    wall_clock = measurement.latency_seconds
    tokens_per_second = measurement.completion_tokens / wall_clock if wall_clock > 0 else None
    return {
        "run_mode": "opencode",
        "task": task,
        "model": model.name,
        "model_id": model.model_id,
        "pinned_revision": model.pinned_revision,
        "provider_type": model.type,
        "endpoint": model.base_url,
        "engine": model.engine,
        "quant": model.quant,
        "provider": model.provider,
        "mode": mode,
        "seed": seed,
        "temperature": temperature,
        "prompt_file": str(prompt_file),
        "raw_response": measurement.response,
        "metrics": {
            "ttft_seconds": measurement.ttft_seconds,
            "wall_clock_seconds": wall_clock,
            "prefill_tokens_per_second": measurement.prefill_tokens_per_second,
            "decode_tokens_per_second": measurement.decode_tokens_per_second,
            "tokens_per_second": tokens_per_second,
        },
        "tokens": {
            "prompt": measurement.prompt_tokens,
            "completion": measurement.completion_tokens,
            "estimated": measurement.token_counts_estimated,
        },
    }


def run_opencode(
    *,
    model: ModelConfig,
    overrides: OpenCodeOverrides,
    mode: RunMode,
    prompts_dir: str | Path,
    results_dir: str | Path,
    seed: int = DEFAULT_SEED,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int | None = None,
    run_path: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[Path, list[tuple[str, CompletionMeasurement]]]:
    """Drive both task prompts against one model end-to-end, capturing each run.

    Returns the JSONL result path and per-task measurements. The same resolved
    model (one provider, one endpoint) serves both tasks so they are measured under
    identical conditions.
    """

    resolved = resolve_model(model, overrides, mode=mode)
    provider = provider_for_model(resolved)
    result_path = run_path or new_run_path(results_dir, prefix=f"opencode-{resolved.name}")
    effective_max_tokens = max_tokens if max_tokens is not None else resolved.max_tokens

    records: list[tuple[str, CompletionMeasurement]] = []
    for task in OPENCODE_TASKS:
        prompt_file, prompt_text = load_prompt(prompts_dir, task)
        if progress is not None:
            progress(f"{resolved.name} {task} [{mode}]: invoking")
        request = ChatRequest(
            prompt=prompt_text,
            temperature=temperature,
            max_tokens=effective_max_tokens,
        )
        measurement = capture_stream_metrics(provider.stream_chat(request), prompt_text)
        append_jsonl(
            result_path,
            build_record(
                task=task,
                model=resolved,
                mode=mode,
                seed=seed,
                temperature=temperature,
                prompt_file=prompt_file,
                measurement=measurement,
            ),
        )
        records.append((task, measurement))
        if progress is not None:
            progress(
                f"{resolved.name} {task} [{mode}]: "
                f"{measurement.completion_tokens} tokens in "
                f"{measurement.latency_seconds:.2f}s"
            )

    return result_path, records
