"""Prefill-vs-context sweep helpers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

from local_code_bench.config import ModelConfig
from local_code_bench.engine_provenance import (
    EngineProvenance,
    engine_fingerprint,
    engine_label,
)
from local_code_bench.metrics import estimate_tokens
from local_code_bench.metrics import capture_stream_metrics
from local_code_bench.provider import ChatRequest, provider_for_model
from local_code_bench.results import append_jsonl

CONTEXT_SIZES = (2000, 8000, 16000, 24000)


def padded_prompt(question: str, target_tokens: int) -> str:
    if target_tokens <= 0:
        raise ValueError("target_tokens must be positive")
    filler = "You are maintaining a Python codebase. "
    prompt = question
    while estimate_tokens(prompt) < target_tokens:
        prompt = filler + prompt
    return prompt


def sweep_prompts(question: str, sizes: tuple[int, ...] = CONTEXT_SIZES) -> list[tuple[int, str]]:
    return [(size, padded_prompt(question, size)) for size in sizes]


def run_sweep(
    *,
    models: list[ModelConfig],
    question: str,
    result_path: Path,
    sizes: tuple[int, ...] = CONTEXT_SIZES,
    engine_provenance: Mapping[str, EngineProvenance] | None = None,
) -> dict[str, int]:
    count = 0
    engines = engine_provenance or {}
    for model in models:
        provenance = engines.get(model.name)
        if model.inferencer is not None and provenance is None:
            raise ValueError(
                f"model '{model.name}' requires exact engine provenance for "
                f"inferencer '{model.inferencer}'"
            )
        provider = provider_for_model(model)
        for size, prompt in sweep_prompts(question, sizes):
            measurement = capture_stream_metrics(
                provider.stream_chat(ChatRequest(prompt=prompt, temperature=0.0)),
                prompt,
            )
            record: dict[str, object] = {
                "run_mode": "sweep",
                "model": model.name,
                "context_tokens": size,
                "prompt_tokens": measurement.prompt_tokens,
                "raw_response": measurement.response,
                "metrics": {
                    "ttft_seconds": measurement.ttft_seconds,
                    "latency_seconds": measurement.latency_seconds,
                    "prefill_tokens_per_second": measurement.prefill_tokens_per_second,
                    "decode_tokens_per_second": measurement.decode_tokens_per_second,
                },
            }
            if provenance is not None:
                record["engine"] = provenance.as_dict()
            append_jsonl(result_path, record)
            count += 1
    return {"sweeps": count}


def summarize_sweep(records: list[dict[str, object]]) -> str:
    grouped: dict[tuple[str, object, str], list[tuple[int, float, float]]] = defaultdict(list)
    for record in records:
        model = record.get("model")
        size = record.get("context_tokens")
        metrics = record.get("metrics")
        if isinstance(model, str) and isinstance(size, int) and isinstance(metrics, dict):
            grouped[
                (
                    model,
                    engine_fingerprint(record.get("engine")),
                    engine_label(record.get("engine")),
                )
            ].append(
                (
                    size,
                    float(metrics.get("ttft_seconds", 0.0) or 0.0),
                    float(metrics.get("prefill_tokens_per_second", 0.0) or 0.0),
                )
            )
    lines = [
        "| Model | Engine | Context Tokens | TTFT | Prefill tok/s |",
        "|---|---|---:|---:|---:|",
    ]
    for (model, _engine, label), items in sorted(grouped.items()):
        for size, ttft, prefill in sorted(items):
            lines.append(
                f"| {model} | {label} | {size} | "
                f"{ttft:.3f} | {prefill:.3f} |"
            )
    lines.append("")
    power_lines = _power_table(records)
    if power_lines:
        lines.extend(power_lines)
        lines.append("")
    lines.append("Finding: compare dense vs MoE rows on this hardware before claiming an advantage.")
    return "\n".join(lines)


def _power_table(records: list[dict[str, object]]) -> list[str]:
    rows: list[tuple[str, float, float, float, float, int]] = []
    for record in records:
        if record.get("record_type") != "power" or not record.get("available"):
            continue
        model = record.get("model")
        if not isinstance(model, str):
            continue
        rows.append(
            (
                model,
                float(record.get("avg_gpu_w", 0.0) or 0.0),
                float(record.get("max_gpu_w", 0.0) or 0.0),
                float(record.get("avg_combined_w", 0.0) or 0.0),
                float(record.get("energy_j", 0.0) or 0.0),
                int(record.get("samples", 0) or 0),
            )
        )
    if not rows:
        return []
    table = [
        "| Model | Avg GPU W | Max GPU W | Avg Combined W | Energy J | Samples |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for model, avg_gpu, max_gpu, avg_combined, energy, samples in sorted(rows):
        table.append(
            f"| {model} | {avg_gpu:.2f} | {max_gpu:.2f} | {avg_combined:.2f} | {energy:.1f} | {samples} |"
        )
    return table
