from __future__ import annotations

import pytest

from local_code_bench.config import ConfigError, load_models


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
    assert models["anthropic-claude-baseline"].pinned_revision == "20250514"
