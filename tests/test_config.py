from __future__ import annotations

import pytest

from local_code_bench.config import (
    AgentConfig,
    ConfigError,
    load_agents,
    load_inferencers,
    load_models,
)


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


def test_load_models_parses_optional_inferencer(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: local
    type: openai
    base_url: http://localhost:8000/v1
    model_id: qwen-local
    pinned_revision: abc123
    inferencer: dflash
    price_per_1k_tokens:
      input: 0.0
      output: 0.0
  - name: cloud
    type: openai
    base_url: https://example.test/v1
    model_id: qwen
    pinned_revision: abc123
    price_per_1k_tokens:
      input: 0.01
      output: 0.02
""",
        encoding="utf-8",
    )

    models = load_models(config_path)

    # A model may declare the engine it needs; existing entries keep None.
    assert models["local"].inferencer == "dflash"
    assert models["cloud"].inferencer is None


def test_default_models_inferencers_line_up_with_ports() -> None:
    """The declared inferencer's port matches the model's base_url port (08.5 AC4)."""
    from urllib.parse import urlparse

    from local_code_bench.config import load_inferencers

    models = load_models("configs/models.yaml")
    inferencers = load_inferencers("configs/inferencers.yaml")

    for model_name in ("local-dflash-qwen", "local-turboquant-qwen-moe", "local-mtplx-qwen"):
        declared = models[model_name].inferencer
        assert declared in inferencers, f"{model_name} declares unknown inferencer {declared!r}"
        assert urlparse(models[model_name].base_url).port == inferencers[declared].port


def test_load_models_parses_extra_body(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: cloud
    type: openai
    base_url: http://localhost:8000/v1
    model_id: qwen
    pinned_revision: abc123
    extra_body:
      reasoning:
        enabled: false
    price_per_1k_tokens:
      input: 0.01
      output: 0.02
""",
        encoding="utf-8",
    )

    models = load_models(config_path)

    assert models["cloud"].extra_body == {"reasoning": {"enabled": False}}


def test_load_models_rejects_non_mapping_extra_body(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: cloud
    type: openai
    base_url: http://localhost:8000/v1
    model_id: qwen
    pinned_revision: abc123
    extra_body: "nope"
    price_per_1k_tokens:
      input: 0.01
      output: 0.02
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="extra_body"):
        load_models(config_path)


def test_load_models_parses_opencode_provenance_fields(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: local
    type: openai
    base_url: http://localhost:8000/v1
    model_id: qwen
    pinned_revision: abc123
    quant: IQ3_XXS
    provider: unsloth
    engine: dflash
    thinking_extra_body:
      reasoning:
        effort: high
    price_per_1k_tokens:
      input: 0.0
      output: 0.0
  - name: cloud
    type: openai
    base_url: https://example.test/v1
    model_id: qwen
    pinned_revision: abc123
    price_per_1k_tokens:
      input: 0.01
      output: 0.02
""",
        encoding="utf-8",
    )

    models = load_models(config_path)

    # Provenance lessons (quant string + Unsloth-vs-Bartowski source) and run-mode
    # knobs are first-class but optional, so legacy entries keep None.
    assert models["local"].quant == "IQ3_XXS"
    assert models["local"].provider == "unsloth"
    assert models["local"].engine == "dflash"
    assert models["local"].thinking_extra_body == {"reasoning": {"effort": "high"}}
    assert models["cloud"].quant is None
    assert models["cloud"].provider is None
    assert models["cloud"].engine is None
    assert models["cloud"].thinking_extra_body is None


def test_load_models_rejects_non_mapping_thinking_extra_body(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: cloud
    type: openai
    base_url: http://localhost:8000/v1
    model_id: qwen
    pinned_revision: abc123
    thinking_extra_body: "nope"
    price_per_1k_tokens:
      input: 0.01
      output: 0.02
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="thinking_extra_body"):
        load_models(config_path)


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
    assert agents["codex"].url is None


def test_load_agents_accepts_registered_harness_type_and_url(tmp_path) -> None:
    from local_code_bench.agents import register_agent_adapter

    class DummyAdapter:
        kind = "dummy"

        def build_command(self, agent: AgentConfig, workspace) -> list[str]:
            return [agent.command]

        def parse_result(self, agent: AgentConfig, workspace, completed) -> dict[str, object]:
            return {"cost_status": "unavailable"}

        def detect(self, agent: AgentConfig):
            raise NotImplementedError

    config_path = tmp_path / "agents.yaml"
    config_path.write_text(
        """
agents:
  - name: custom
    type: dummy
    command: dummy-agent
    sandbox: workspace-write
    url: https://example.test/dummy
""",
        encoding="utf-8",
    )

    with register_agent_adapter(DummyAdapter()):
        agents = load_agents(config_path)

    assert agents["custom"].type == "dummy"
    assert agents["custom"].url == "https://example.test/dummy"


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


def test_load_models_reports_missing_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="model config not found"):
        load_models(tmp_path / "absent.yaml")


def test_load_models_reports_invalid_yaml(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text("models: [unterminated", encoding="utf-8")

    with pytest.raises(ConfigError, match="invalid YAML"):
        load_models(config_path)


def test_load_models_requires_top_level_mapping(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="top-level mapping"):
        load_models(config_path)


def test_load_models_requires_models_list(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text("models: not-a-list\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="'models' must be a list"):
        load_models(config_path)


def test_load_models_rejects_non_mapping_entry(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text("models:\n  - just-a-string\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"models\[0\] must be a mapping"):
        load_models(config_path)


def test_load_models_rejects_unknown_type(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: bad
    type: bedrock
    base_url: http://localhost:8000/v1
    model_id: qwen
    pinned_revision: abc123
    price_per_1k_tokens:
      input: 0.0
      output: 0.0
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="must be 'openai' or 'anthropic'"):
        load_models(config_path)


def test_load_models_requires_price_mapping(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: bad
    type: openai
    base_url: http://localhost:8000/v1
    model_id: qwen
    pinned_revision: abc123
    price_per_1k_tokens: free
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="price_per_1k_tokens must be a mapping"):
        load_models(config_path)


def test_load_models_rejects_negative_price(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: bad
    type: openai
    base_url: http://localhost:8000/v1
    model_id: qwen
    pinned_revision: abc123
    price_per_1k_tokens:
      input: -1
      output: 0.0
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="must be a non-negative number"):
        load_models(config_path)


def test_load_models_rejects_duplicate_name(tmp_path) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: dup
    type: openai
    base_url: http://localhost:8000/v1
    model_id: qwen
    pinned_revision: abc123
    price_per_1k_tokens:
      input: 0.0
      output: 0.0
  - name: dup
    type: openai
    base_url: http://localhost:8001/v1
    model_id: qwen2
    pinned_revision: def456
    price_per_1k_tokens:
      input: 0.0
      output: 0.0
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="duplicates 'dup'"):
        load_models(config_path)


def test_load_agents_reports_missing_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="agent config not found"):
        load_agents(tmp_path / "absent.yaml")


def test_load_agents_reports_invalid_yaml(tmp_path) -> None:
    config_path = tmp_path / "agents.yaml"
    config_path.write_text("agents: [unterminated", encoding="utf-8")

    with pytest.raises(ConfigError, match="invalid YAML"):
        load_agents(config_path)


def test_load_agents_requires_agents_list(tmp_path) -> None:
    config_path = tmp_path / "agents.yaml"
    config_path.write_text("agents: nope\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="'agents' must be a list"):
        load_agents(config_path)


def test_load_agents_rejects_non_mapping_entry(tmp_path) -> None:
    config_path = tmp_path / "agents.yaml"
    config_path.write_text("agents:\n  - just-a-string\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"agents\[0\] must be a mapping"):
        load_agents(config_path)


def test_load_agents_rejects_unknown_type(tmp_path) -> None:
    config_path = tmp_path / "agents.yaml"
    config_path.write_text(
        """
agents:
  - name: claude
    type: claude
    command: claude
    sandbox: workspace-write
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="supported types: codex"):
        load_agents(config_path)


def test_load_agents_rejects_duplicate_name(tmp_path) -> None:
    config_path = tmp_path / "agents.yaml"
    config_path.write_text(
        """
agents:
  - name: codex
    type: codex
    command: codex
    sandbox: workspace-write
  - name: codex
    type: codex
    command: codex2
    sandbox: read-only
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="duplicates 'codex'"):
        load_agents(config_path)


def test_load_inferencers_reports_missing_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="inferencer config not found"):
        load_inferencers(tmp_path / "absent.yaml")


def test_load_inferencers_requires_inferencers_list(tmp_path) -> None:
    config_path = tmp_path / "inferencers.yaml"
    config_path.write_text("inferencers: nope\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="'inferencers' must be a list"):
        load_inferencers(config_path)
