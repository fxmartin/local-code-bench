"""Configuration loading for endpoint benchmark targets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

ModelType = Literal["openai", "anthropic"]


class ConfigError(ValueError):
    """Raised when benchmark configuration is invalid."""


@dataclass(frozen=True)
class TokenPrices:
    input: float
    output: float


@dataclass(frozen=True)
class ModelConfig:
    name: str
    type: ModelType
    base_url: str
    model_id: str
    pinned_revision: str
    price_per_1k_tokens: TokenPrices
    api_key_env: str | None = None
    concurrency: int = 1
    max_tokens: int | None = None
    extra_body: dict[str, Any] | None = None
    inferencer: str | None = None


AgentType = Literal["codex"]


@dataclass(frozen=True)
class AgentConfig:
    name: str
    type: AgentType
    command: str
    sandbox: str
    timeout_seconds: float
    model: str | None = None
    profile: str | None = None


Lifecycle = Literal["server", "app"]
DetectKind = Literal["binary", "module", "app"]


@dataclass(frozen=True)
class InferencerConfig:
    """A macOS inference engine the harness can detect and (for servers) manage."""

    name: str
    lifecycle: Lifecycle
    detect_kind: DetectKind
    detect_target: str
    port: int
    health_url: str
    start: tuple[str, ...] | None = None
    stop: tuple[str, ...] | None = None
    url: str | None = None


def resolve_health_url(cfg: InferencerConfig) -> str:
    """Substitute `{port}` in an inferencer's health URL template."""

    return cfg.health_url.format(port=cfg.port)


def load_models(path: str | Path) -> dict[str, ModelConfig]:
    """Load and validate endpoint model configs from YAML."""

    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"model config not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("models.yaml must contain a top-level mapping")

    entries = raw.get("models")
    if not isinstance(entries, list):
        raise ConfigError("models.yaml field 'models' must be a list")

    models: dict[str, ModelConfig] = {}
    for index, entry in enumerate(entries):
        model = _parse_model(entry, index)
        if model.name in models:
            raise ConfigError(f"models[{index}].name duplicates '{model.name}'")
        models[model.name] = model

    return models


def load_agents(path: str | Path) -> dict[str, AgentConfig]:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"agent config not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("agents"), list):
        raise ConfigError("agents.yaml field 'agents' must be a list")
    agents: dict[str, AgentConfig] = {}
    for index, entry in enumerate(raw["agents"]):
        agent = _parse_agent(entry, index)
        if agent.name in agents:
            raise ConfigError(f"agents[{index}].name duplicates '{agent.name}'")
        agents[agent.name] = agent
    return agents


def load_inferencers(path: str | Path) -> dict[str, InferencerConfig]:
    """Load and validate inference-engine configs from YAML."""

    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"inferencer config not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("inferencers"), list):
        raise ConfigError("inferencers.yaml field 'inferencers' must be a list")
    inferencers: dict[str, InferencerConfig] = {}
    for index, entry in enumerate(raw["inferencers"]):
        inferencer = _parse_inferencer(entry, index)
        if inferencer.name in inferencers:
            raise ConfigError(f"inferencers[{index}].name duplicates '{inferencer.name}'")
        inferencers[inferencer.name] = inferencer
    return inferencers


def _parse_inferencer(entry: Any, index: int) -> InferencerConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"inferencers[{index}] must be a mapping")

    lifecycle = _required_str(entry, "lifecycle", index, root="inferencers")
    if lifecycle not in {"server", "app"}:
        raise ConfigError(f"inferencers[{index}].lifecycle must be 'server' or 'app'")

    detect = entry.get("detect")
    if not isinstance(detect, dict):
        raise ConfigError(f"inferencers[{index}].detect must be a mapping")
    kinds = [kind for kind in ("binary", "module", "app") if kind in detect]
    if len(kinds) != 1:
        raise ConfigError(
            f"inferencers[{index}].detect must have exactly one of binary/module/app"
        )
    detect_kind = kinds[0]
    detect_target = _required_str(detect, detect_kind, index, root="inferencers")

    port = entry.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or port < 1:
        raise ConfigError(f"inferencers[{index}].port must be a positive integer")

    start = _optional_command(entry, "start", index)
    stop = _optional_command(entry, "stop", index)
    if lifecycle == "server" and start is None:
        raise ConfigError(f"inferencers[{index}] server lifecycle requires a 'start' command")
    if lifecycle == "app" and (start is not None or stop is not None):
        raise ConfigError(f"inferencers[{index}] app lifecycle must not define start/stop")

    return InferencerConfig(
        name=_required_str(entry, "name", index, root="inferencers"),
        lifecycle=lifecycle,  # type: ignore[arg-type]
        detect_kind=detect_kind,  # type: ignore[arg-type]
        detect_target=detect_target,
        port=port,
        health_url=_required_str(entry, "health_url", index, root="inferencers"),
        start=start,
        stop=stop,
        url=_optional_str(entry, "url", index, root="inferencers"),
    )


def _optional_command(
    entry: dict[str, Any],
    field: str,
    index: int,
) -> tuple[str, ...] | None:
    value = entry.get(field)
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise ConfigError(
            f"inferencers[{index}].{field} must be a non-empty list of strings when set"
        )
    if not all(isinstance(arg, str) and arg.strip() for arg in value):
        raise ConfigError(
            f"inferencers[{index}].{field} must be a non-empty list of strings when set"
        )
    return tuple(value)


def _parse_model(entry: Any, index: int) -> ModelConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"models[{index}] must be a mapping")

    name = _required_str(entry, "name", index)
    model_type = _required_str(entry, "type", index)
    if model_type not in {"openai", "anthropic"}:
        raise ConfigError(f"models[{index}].type must be 'openai' or 'anthropic'")

    prices = entry.get("price_per_1k_tokens")
    if not isinstance(prices, dict):
        raise ConfigError(f"models[{index}].price_per_1k_tokens must be a mapping")

    return ModelConfig(
        name=name,
        type=model_type,  # type: ignore[arg-type]
        base_url=_required_str(entry, "base_url", index).rstrip("/"),
        model_id=_required_str(entry, "model_id", index),
        pinned_revision=_required_str(entry, "pinned_revision", index),
        price_per_1k_tokens=TokenPrices(
            input=_required_number(prices, "input", index, "price_per_1k_tokens"),
            output=_required_number(prices, "output", index, "price_per_1k_tokens"),
        ),
        api_key_env=_optional_str(entry, "api_key_env", index),
        concurrency=_optional_positive_int(entry, "concurrency", index, default=1),
        max_tokens=_optional_positive_int(entry, "max_tokens", index, default=None),
        extra_body=_optional_mapping(entry, "extra_body", index),
        inferencer=_optional_str(entry, "inferencer", index),
    )


def _parse_agent(entry: Any, index: int) -> AgentConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"agents[{index}] must be a mapping")
    agent_type = _required_str(entry, "type", index, root="agents")
    if agent_type != "codex":
        raise ConfigError(f"agents[{index}].type must be 'codex'")
    timeout = entry.get("timeout_seconds", 600)
    if not isinstance(timeout, int | float) or timeout <= 0:
        raise ConfigError(f"agents[{index}].timeout_seconds must be a positive number")
    return AgentConfig(
        name=_required_str(entry, "name", index, root="agents"),
        type="codex",
        command=_required_str(entry, "command", index, root="agents"),
        sandbox=_required_str(entry, "sandbox", index, root="agents"),
        timeout_seconds=float(timeout),
        model=_optional_str(entry, "model", index, root="agents"),
        profile=_optional_str(entry, "profile", index, root="agents"),
    )


def _required_str(
    entry: dict[str, Any],
    field: str,
    index: int,
    *,
    root: str = "models",
) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{root}[{index}].{field} must be a non-empty string")
    return value


def _optional_str(
    entry: dict[str, Any],
    field: str,
    index: int,
    *,
    root: str = "models",
) -> str | None:
    value = entry.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{root}[{index}].{field} must be a non-empty string when set")
    return value


def _optional_positive_int(
    entry: dict[str, Any],
    field: str,
    index: int,
    *,
    default: int | None,
    root: str = "models",
) -> int | None:
    value = entry.get(field)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{root}[{index}].{field} must be a positive integer when set")
    return value


def _optional_mapping(
    entry: dict[str, Any],
    field: str,
    index: int,
    *,
    root: str = "models",
) -> dict[str, Any] | None:
    value = entry.get(field)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError(f"{root}[{index}].{field} must be a mapping when set")
    return value


def _required_number(
    entry: dict[str, Any],
    field: str,
    index: int,
    parent: str,
) -> float:
    value = entry.get(field)
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        raise ConfigError(f"models[{index}].{parent}.{field} must be a non-negative number")
    return float(value)
