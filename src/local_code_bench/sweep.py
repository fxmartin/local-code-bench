"""Prefill-vs-context sweep helpers."""

from __future__ import annotations

from collections import defaultdict

from local_code_bench.metrics import estimate_tokens

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


def summarize_sweep(records: list[dict[str, object]]) -> str:
    grouped: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for record in records:
        model = record.get("model")
        size = record.get("context_tokens")
        metrics = record.get("metrics")
        if isinstance(model, str) and isinstance(size, int) and isinstance(metrics, dict):
            grouped[model].append(
                (
                    size,
                    float(metrics.get("ttft_seconds", 0.0) or 0.0),
                    float(metrics.get("prefill_tokens_per_second", 0.0) or 0.0),
                )
            )
    lines = [
        "| Model | Context Tokens | TTFT | Prefill tok/s |",
        "|---|---:|---:|---:|",
    ]
    for model, items in sorted(grouped.items()):
        for size, ttft, prefill in sorted(items):
            lines.append(f"| {model} | {size} | {ttft:.3f} | {prefill:.3f} |")
    lines.append("")
    lines.append("Finding: compare dense vs MoE rows on this hardware before claiming an advantage.")
    return "\n".join(lines)
