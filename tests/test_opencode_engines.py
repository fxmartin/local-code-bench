from __future__ import annotations

import pytest

from local_code_bench.config import ConfigError, load_inferencers
from local_code_bench.opencode.engines import ENGINE_ENDPOINTS, endpoint_for_engine


def test_endpoint_for_known_engine_returns_v1_url() -> None:
    assert endpoint_for_engine("dflash") == "http://127.0.0.1:8000/v1"
    assert endpoint_for_engine("ollama") == "http://127.0.0.1:11434/v1"
    assert endpoint_for_engine("lm-studio") == "http://127.0.0.1:1234/v1"


def test_endpoint_map_covers_the_ten_locked_engines() -> None:
    # The 10 engines whose default ports were locked with FX for --engine.
    assert set(ENGINE_ENDPOINTS) == {
        "dflash",
        "turboquant",
        "mlx-lm",
        "llama-cpp",
        "ollama",
        "mlc-llm",
        "vllm-mlx",
        "exo",
        "lm-studio",
        "gpt4all",
    }


def test_every_endpoint_is_a_loopback_v1_url() -> None:
    for url in ENGINE_ENDPOINTS.values():
        assert url.startswith("http://127.0.0.1:")
        assert url.endswith("/v1")


def test_unknown_engine_raises_with_available_list() -> None:
    with pytest.raises(ConfigError, match="unknown engine 'nope'"):
        endpoint_for_engine("nope")


def test_engine_ports_match_inferencer_registry() -> None:
    """Each mapped engine's port agrees with the inferencer registry (no drift)."""
    from urllib.parse import urlparse

    inferencers = load_inferencers("configs/inferencers.yaml")
    for name, url in ENGINE_ENDPOINTS.items():
        assert name in inferencers, f"{name} missing from inferencer registry"
        assert urlparse(url).port == inferencers[name].port
