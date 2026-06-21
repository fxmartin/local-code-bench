from __future__ import annotations

import pytest

from local_code_bench.config import ConfigError, load_agents, load_models


def test_load_models_validates_endpoint_config(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: local
    type: openai
    base_url: http://localhost:8000/v1/
    model_id: qwen
    pinned_revision: abc123
    api_key_env: OPENROUTER_API_KEY
    price_per_1k_tokens:
      input: 0.01
      output: 0.02
""",
        encoding="utf-8",
    )

    models = load_models(config_path)

    assert models["local"].base_url == "http://localhost:8000/v1"
    assert models["local"].price_per_1k_tokens.input == 0.01
    assert models["local"].api_key_env == "OPENROUTER_API_KEY"


def test_load_models_reports_missing_field(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: local
    type: openai
    base_url: http://localhost:8000/v1
    pinned_revision: abc123
    price_per_1k_tokens:
      input: 0.01
      output: 0.02
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="model_id"):
        load_models(config_path)


def test_default_models_config_loads() -> None:
    models = load_models("configs/models.yaml")

    assert "openrouter-glm-4.6" in models
    assert models["anthropic-claude-baseline"].pinned_revision == "claude-sonnet-4-6"


def test_load_models_parses_concurrency_and_max_tokens(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: cloud
    type: openai
    base_url: http://localhost:8000/v1
    model_id: qwen
    pinned_revision: abc123
    concurrency: 12
    max_tokens: 512
    price_per_1k_tokens:
      input: 0.01
      output: 0.02
  - name: local
    type: openai
    base_url: http://localhost:8000/v1
    model_id: qwen-local
    pinned_revision: abc123
    price_per_1k_tokens:
      input: 0.0
      output: 0.0
""",
        encoding="utf-8",
    )

    models = load_models(config_path)

    assert models["cloud"].concurrency == 12
    assert models["cloud"].max_tokens == 512
    # Defaults keep local servers serial and uncapped at config level.
    assert models["local"].concurrency == 1
    assert models["local"].max_tokens is None


def test_load_models_rejects_non_positive_concurrency(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: cloud
    type: openai
    base_url: http://localhost:8000/v1
    model_id: qwen
    pinned_revision: abc123
    concurrency: 0
    price_per_1k_tokens:
      input: 0.01
      output: 0.02
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="concurrency"):
        load_models(config_path)


def test_load_agents_validates_codex_config(tmp_path) -> None:
    config_path = tmp_path / "agents.yaml"
    config_path.write_text(
        """
agents:
  - name: codex
    type: codex
    command: codex
    sandbox: workspace-write
    timeout_seconds: 30
    model: gpt-5
    profile: default
""",
        encoding="utf-8",
    )

    agents = load_agents(config_path)

    assert agents["codex"].timeout_seconds == 30.0
    assert agents["codex"].model == "gpt-5"
    assert agents["codex"].profile == "default"


def test_load_agents_reports_invalid_timeout(tmp_path) -> None:
    config_path = tmp_path / "agents.yaml"
    config_path.write_text(
        """
agents:
  - name: codex
    type: codex
    command: codex
    sandbox: workspace-write
    timeout_seconds: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="timeout_seconds"):
        load_agents(config_path)
