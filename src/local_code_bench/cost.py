"""Cost calculation for endpoint runs."""

from __future__ import annotations

from local_code_bench.config import ModelConfig


def calculate_cost_usd(model: ModelConfig, prompt_tokens: int, completion_tokens: int) -> float:
    return (
        prompt_tokens * model.price_per_1k_tokens.input
        + completion_tokens * model.price_per_1k_tokens.output
    ) / 1000
