from __future__ import annotations

from dataclasses import replace

from local_code_bench.config import ModelConfig, TokenPrices
from local_code_bench.endpoint_provenance import endpoint_provider_name


def _model(*, model_type: str = "openai", base_url: str) -> ModelConfig:
    return ModelConfig(
        name="model",
        type=model_type,  # type: ignore[arg-type]
        base_url=base_url,
        model_id="model",
        pinned_revision="test",
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
    )


def test_openrouter_provider_uses_stable_domain() -> None:
    assert (
        endpoint_provider_name(_model(base_url="https://openrouter.ai/api/v1"))
        == "openrouter.ai"
    )


def test_direct_anthropic_provider_uses_product_name() -> None:
    assert (
        endpoint_provider_name(
            _model(model_type="anthropic", base_url="https://api.anthropic.com/v1")
        )
        == "anthropic"
    )


def test_generic_compatible_provider_uses_hostname() -> None:
    assert (
        endpoint_provider_name(_model(base_url="https://gateway.example.test/v1"))
        == "gateway.example.test"
    )


def test_local_inferencer_has_no_endpoint_provider() -> None:
    local = _model(base_url="http://127.0.0.1:11434/v1")
    local = replace(local, inferencer="ollama")

    assert endpoint_provider_name(local) is None
