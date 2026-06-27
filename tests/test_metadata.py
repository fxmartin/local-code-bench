"""Run-metadata header — including Epic-12 Story 12.5-001 tier provenance.

Covers :func:`local_code_bench.metadata.run_metadata`: the stable base header and
the optional ``tier`` block that records how a benchmark obtained its model across
storage tiers, so the leaderboard/dashboard can caveat external-served speed.
"""

from __future__ import annotations

from local_code_bench.config import ModelConfig, TokenPrices
from local_code_bench.metadata import run_metadata


def _model() -> ModelConfig:
    return ModelConfig(
        name="qwen",
        type="openai",
        base_url="http://127.0.0.1:1234/v1",
        model_id="qwen2.5-coder",
        pinned_revision="rev0",
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
    )


def test_run_metadata_omits_tier_by_default() -> None:
    meta = run_metadata(models=[_model()], suite="canary")

    assert "tier" not in meta
    assert meta["record_type"] == "metadata"
    assert meta["suite"] == "canary"
    assert "qwen" in meta["models"]  # type: ignore[operator]


def test_run_metadata_includes_tier_when_supplied() -> None:
    tier = {"served_tier": "external", "promoted": False, "served_from_external": True}

    meta = run_metadata(models=[_model()], suite="canary", tier=tier)

    assert meta["tier"] == tier
