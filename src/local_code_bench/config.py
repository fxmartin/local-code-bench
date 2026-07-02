"""Configuration loading for endpoint benchmark targets."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    quant: str | None = None
    provider: str | None = None
    engine: str | None = None
    thinking_extra_body: dict[str, Any] | None = None


AgentType = str


@dataclass(frozen=True)
class AgentConfig:
    name: str
    type: AgentType
    command: str
    sandbox: str
    timeout_seconds: float
    model: str | None = None
    profile: str | None = None
    url: str | None = None


Lifecycle = Literal["server", "app"]
DetectKind = Literal["binary", "module", "app"]
StoreFormat = Literal["gguf", "ollama", "hf-safetensors", "mlx"]

#: On-disk model-store formats the inventory scanner (Epic-11) understands.
STORE_FORMATS: frozenset[str] = frozenset({"gguf", "ollama", "hf-safetensors", "mlx"})

#: Default sentinel filename written into the external-repo root so the *same*
#: repo is recognised across remounts (and a coincidentally-present empty mount
#: path is not mistaken for it).
DEFAULT_VOLUME_MARKER = ".local-code-bench-external"

#: Default per-format subdirectory layout under the external-repo root. The
#: external tier mirrors the local per-format store layout (one subdir per
#: format) so Epic-11's scan strategies apply unchanged against a different root.
DEFAULT_EXTERNAL_SUBPATHS: dict[str, str] = {fmt: fmt for fmt in sorted(STORE_FORMATS)}


@dataclass(frozen=True)
class ExternalRepoConfig:
    """Second-tier (external SSD) model repository (Epic-12, Story 12.1-001).

    ``root`` is the repository root on the external volume; it keeps any ``~``
    verbatim so expansion happens at availability-check time (mirroring the
    Epic-11 scanner). The repo mirrors the local per-format store layout — each
    store format lives under ``root/<subpath>`` so the Epic-11 scan strategies
    run unchanged. ``volume_marker`` is a sentinel file written into ``root`` so
    the same repo is recognised across remounts. The tier is optional: a config
    without an ``external_repo`` block stays a valid single-tier config.
    """

    root: str
    volume_marker: str = DEFAULT_VOLUME_MARKER
    subpaths: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_EXTERNAL_SUBPATHS))


@dataclass(frozen=True)
class AutoTierConfig:
    """Auto-tiering policy: a local disk budget plus pinned models (Epic-12, 12.4-001).

    The budget is expressed in GiB and may set ``max_local_gb`` (the most space the
    local tier's models may occupy), ``min_free_gb`` (the least free disk space to
    keep on the local volume), or both — at least one is required. ``pins`` lists
    model names that must never be auto-evicted, even when that means the budget
    cannot be fully met. The block is optional: a config without an ``auto_tier``
    block leaves auto-tiering disabled.
    """

    max_local_gb: float | None = None
    min_free_gb: float | None = None
    pins: tuple[str, ...] = ()


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
    # Epic-11 model inventory: where this engine keeps downloaded models, and the
    # on-disk format so the scanner can read it with the right strategy. Optional
    # and defaulted so pre-Epic-11 entries stay valid; both are set together.
    model_store: tuple[str, ...] | None = None
    store_format: StoreFormat | None = None


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


def load_external_repo(path: str | Path) -> ExternalRepoConfig | None:
    """Load the optional external-tier repo config from an inferencers YAML.

    Returns ``None`` when the file declares no ``external_repo`` block, so an
    existing single-tier config remains valid. Raises :class:`ConfigError` for a
    missing/invalid file or a malformed ``external_repo`` block.
    """

    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"external repo config not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if raw is not None and not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a top-level mapping")
    entry = raw.get("external_repo") if isinstance(raw, dict) else None
    if entry is None:
        return None
    return _parse_external_repo(entry)


def _parse_external_repo(entry: Any) -> ExternalRepoConfig:
    if not isinstance(entry, dict):
        raise ConfigError("external_repo must be a mapping")

    root = entry.get("root")
    if not isinstance(root, str) or not root.strip():
        raise ConfigError("external_repo.root must be a non-empty string")

    marker = entry.get("volume_marker", DEFAULT_VOLUME_MARKER)
    if not isinstance(marker, str) or not marker.strip() or "/" in marker:
        raise ConfigError("external_repo.volume_marker must be a non-empty filename (no '/')")

    return ExternalRepoConfig(
        root=root.strip(),
        volume_marker=marker.strip(),
        subpaths=_parse_external_subpaths(entry.get("subpaths")),
    )


def _parse_external_subpaths(value: Any) -> dict[str, str]:
    """Merge any per-format subpath overrides onto the default mirrored layout."""

    subpaths = dict(DEFAULT_EXTERNAL_SUBPATHS)
    if value is None:
        return subpaths
    if not isinstance(value, dict):
        raise ConfigError("external_repo.subpaths must be a mapping of format -> subdirectory")
    for fmt, sub in value.items():
        if fmt not in STORE_FORMATS:
            allowed = " | ".join(sorted(STORE_FORMATS))
            raise ConfigError(f"external_repo.subpaths key '{fmt}' must be one of: {allowed}")
        if not isinstance(sub, str) or not sub.strip() or sub.strip().startswith("/"):
            raise ConfigError(f"external_repo.subpaths['{fmt}'] must be a non-empty relative path")
        subpaths[fmt] = sub.strip()
    return subpaths


def load_autotier(path: str | Path) -> AutoTierConfig | None:
    """Load the optional auto-tiering policy from an inferencers YAML.

    Returns ``None`` when the file declares no ``auto_tier`` block, so a config
    without auto-tiering stays valid. Raises :class:`ConfigError` for a
    missing/invalid file or a malformed ``auto_tier`` block.
    """

    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"auto-tier config not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if raw is not None and not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a top-level mapping")
    entry = raw.get("auto_tier") if isinstance(raw, dict) else None
    if entry is None:
        return None
    return _parse_auto_tier(entry)


def _parse_auto_tier(entry: Any) -> AutoTierConfig:
    if not isinstance(entry, dict):
        raise ConfigError("auto_tier must be a mapping")

    max_local_gb = _optional_positive_float(entry, "max_local_gb")
    min_free_gb = _optional_positive_float(entry, "min_free_gb")
    if max_local_gb is None and min_free_gb is None:
        raise ConfigError("auto_tier requires at least one of max_local_gb or min_free_gb")

    return AutoTierConfig(
        max_local_gb=max_local_gb,
        min_free_gb=min_free_gb,
        pins=_parse_pins(entry.get("pins")),
    )


def _optional_positive_float(entry: dict[str, Any], field: str) -> float | None:
    """Parse an optional strictly-positive GiB number, or ``None`` when absent."""

    value = entry.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"auto_tier.{field} must be a positive number of GiB")
    return float(value)


def _parse_pins(value: Any) -> tuple[str, ...]:
    """Parse the optional list of pinned model names."""

    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError("auto_tier.pins must be a list of model names")
    pins: list[str] = []
    for name in value:
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("auto_tier.pins entries must be non-empty model names")
        pins.append(name.strip())
    return tuple(pins)


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
        raise ConfigError(f"inferencers[{index}].detect must have exactly one of binary/module/app")
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

    model_store = _optional_store_paths(entry, "model_store", index)
    store_format = _optional_store_format(entry, "format", index)
    if (model_store is None) != (store_format is None):
        raise ConfigError(f"inferencers[{index}] model_store and format must be set together")

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
        model_store=model_store,
        store_format=store_format,  # type: ignore[arg-type]
    )


def _optional_store_paths(
    entry: dict[str, Any],
    field: str,
    index: int,
) -> tuple[str, ...] | None:
    """Parse `model_store` as a single path string or a non-empty list of them."""

    value = entry.get(field)
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ConfigError(f"inferencers[{index}].{field} must be a path or non-empty list of paths")
    if not all(isinstance(path, str) and path.strip() for path in value):
        raise ConfigError(f"inferencers[{index}].{field} must be a path or non-empty list of paths")
    return tuple(value)


def _optional_store_format(
    entry: dict[str, Any],
    field: str,
    index: int,
) -> str | None:
    value = entry.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or value not in STORE_FORMATS:
        allowed = " | ".join(sorted(STORE_FORMATS))
        raise ConfigError(f"inferencers[{index}].{field} must be one of: {allowed}")
    return value


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
        quant=_optional_str(entry, "quant", index),
        provider=_optional_str(entry, "provider", index),
        engine=_optional_str(entry, "engine", index),
        thinking_extra_body=_optional_mapping(entry, "thinking_extra_body", index),
    )


def _parse_agent(entry: Any, index: int) -> AgentConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"agents[{index}] must be a mapping")
    agent_type = _required_str(entry, "type", index, root="agents")
    from local_code_bench.agents import supported_harness_kinds

    supported = supported_harness_kinds()
    if agent_type not in supported:
        allowed = ", ".join(supported) or "(none)"
        raise ConfigError(f"agents[{index}].type '{agent_type}' is not registered; supported types: {allowed}")
    timeout = entry.get("timeout_seconds", 600)
    if not isinstance(timeout, int | float) or timeout <= 0:
        raise ConfigError(f"agents[{index}].timeout_seconds must be a positive number")
    return AgentConfig(
        name=_required_str(entry, "name", index, root="agents"),
        type=agent_type,
        command=_required_str(entry, "command", index, root="agents"),
        sandbox=_required_str(entry, "sandbox", index, root="agents"),
        timeout_seconds=float(timeout),
        model=_optional_str(entry, "model", index, root="agents"),
        profile=_optional_str(entry, "profile", index, root="agents"),
        url=_optional_str(entry, "url", index, root="agents"),
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
