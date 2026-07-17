"""Configuration loading for endpoint benchmark targets."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
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
    anthropic_base_url: str | None = None
    anthropic_api_key_env: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    inferencer: str | None = None


Lifecycle = Literal["server", "app"]
DetectKind = Literal["binary", "module", "app"]
StoreFormat = Literal["ollama", "hf-safetensors"]

#: On-disk model-store formats the inventory scanner (Epic-11) understands.
STORE_FORMATS: frozenset[str] = frozenset({"ollama", "hf-safetensors"})

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


@dataclass(frozen=True)
class OptimizerConfig:
    """A context-optimization proxy the harness can detect and drive (Epic-13).

    The proxy sits between the harness and an inference engine: ``start`` is an
    argv template whose ``{port}`` (the proxy's own listen port) and
    ``{upstream}`` (the active inferencer's base URL) are substituted at launch
    time, so the proxy is always wired to a real engine. Detection is read-only —
    the harness never installs a proxy; ``url`` is the manual-install reference
    surfaced when one is missing.
    """

    name: str
    detect_kind: DetectKind
    detect_target: str
    port: int
    health_url: str
    start: tuple[str, ...]
    url: str | None = None


def resolve_health_url(cfg: InferencerConfig | OptimizerConfig) -> str:
    """Substitute `{port}` in an inferencer's or optimizer's health URL template."""

    return cfg.health_url.format(port=cfg.port)


def resolve_optimizer_start(cfg: OptimizerConfig, upstream: str) -> tuple[str, ...]:
    """Substitute `{port}` and `{upstream}` in an optimizer's start template.

    ``upstream`` is the active inferencer's base URL, kept distinct from
    ``{port}`` (the proxy's own listen port) so the chained lifecycle can fill
    it from whichever engine is running.
    """

    return tuple(arg.format(port=cfg.port, upstream=upstream) for arg in cfg.start)


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


def load_optimizers(path: str | Path) -> dict[str, OptimizerConfig]:
    """Load and validate context-optimization proxy configs from YAML."""

    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"optimizer config not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("optimizers"), list):
        raise ConfigError("optimizers.yaml field 'optimizers' must be a list")
    optimizers: dict[str, OptimizerConfig] = {}
    for index, entry in enumerate(raw["optimizers"]):
        optimizer = _parse_optimizer(entry, index)
        if optimizer.name in optimizers:
            raise ConfigError(f"optimizers[{index}].name duplicates '{optimizer.name}'")
        optimizers[optimizer.name] = optimizer
    return optimizers


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

    detect_kind, detect_target = _parse_detect(entry, index, root="inferencers")
    port = _required_port(entry, index, root="inferencers")

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


def _parse_optimizer(entry: Any, index: int) -> OptimizerConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"optimizers[{index}] must be a mapping")

    detect_kind, detect_target = _parse_detect(entry, index, root="optimizers")
    port = _required_port(entry, index, root="optimizers")

    start = _optional_command(entry, "start", index, root="optimizers")
    if start is None:
        raise ConfigError(f"optimizers[{index}] requires a 'start' command")

    return OptimizerConfig(
        name=_required_str(entry, "name", index, root="optimizers"),
        detect_kind=detect_kind,  # type: ignore[arg-type]
        detect_target=detect_target,
        port=port,
        health_url=_required_str(entry, "health_url", index, root="optimizers"),
        start=start,
        url=_optional_str(entry, "url", index, root="optimizers"),
    )


def _parse_detect(entry: dict[str, Any], index: int, *, root: str) -> tuple[str, str]:
    """Parse a `detect` mapping with exactly one of binary/module/app."""

    detect = entry.get("detect")
    if not isinstance(detect, dict):
        raise ConfigError(f"{root}[{index}].detect must be a mapping")
    kinds = [kind for kind in ("binary", "module", "app") if kind in detect]
    if len(kinds) != 1:
        raise ConfigError(f"{root}[{index}].detect must have exactly one of binary/module/app")
    detect_kind = kinds[0]
    return detect_kind, _required_str(detect, detect_kind, index, root=root)


def _required_port(entry: dict[str, Any], index: int, *, root: str) -> int:
    port = entry.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or port < 1:
        raise ConfigError(f"{root}[{index}].port must be a positive integer")
    return port


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
    *,
    root: str = "inferencers",
) -> tuple[str, ...] | None:
    value = entry.get(field)
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{root}[{index}].{field} must be a non-empty list of strings when set")
    if not all(isinstance(arg, str) and arg.strip() for arg in value):
        raise ConfigError(f"{root}[{index}].{field} must be a non-empty list of strings when set")
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

    concurrency = _optional_positive_int(entry, "concurrency", index, default=1)
    if concurrency is None:
        raise ConfigError(f"models[{index}].concurrency must be a positive integer")
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
        concurrency=concurrency,
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
        raise ConfigError(
            f"agents[{index}].type '{agent_type}' is not registered; supported types: {allowed}"
        )
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
        anthropic_base_url=_optional_str(entry, "anthropic_base_url", index, root="agents"),
        anthropic_api_key_env=_optional_str(entry, "anthropic_api_key_env", index, root="agents"),
        base_url=_optional_url(entry, "base_url", index, root="agents"),
        api_key_env=_optional_str(entry, "api_key_env", index, root="agents"),
        system_prompt=_optional_str(entry, "system_prompt", index, root="agents"),
        append_system_prompt=_optional_str(entry, "append_system_prompt", index, root="agents"),
        inferencer=_optional_str(entry, "inferencer", index, root="agents"),
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


def _optional_url(
    entry: dict[str, Any],
    field: str,
    index: int,
    *,
    root: str = "models",
) -> str | None:
    value = _optional_str(entry, field, index, root=root)
    if value is None:
        return None
    return value.rstrip("/")


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


# ---------------------------------------------------------------------------
# comparison-axis catalog (Epic-17, story 17.1-002)
# ---------------------------------------------------------------------------

PairingKey = Literal["base_model", "base_model_engine", "suite_context"]

#: How an axis pairs configurations across its cohorts: same base model (the
#: Epic-11 normalization), same base model on the same engine, or any
#: configurations sharing a (suite, suite version, hardware tag) context.
PAIRING_KEYS: frozenset[str] = frozenset({"base_model", "base_model_engine", "suite_context"})

#: Metrics a verdict rule may threshold — the keys of the compare module's
#: per-cohort verdict inputs, so every declared rule is computable from the
#: aggregates the dashboard already serves.
VERDICT_METRICS: frozenset[str] = frozenset(
    {
        "pass_at_1",
        "median_ttft_seconds",
        "p95_ttft_seconds",
        "median_prefill_tokens_per_second",
        "median_decode_tokens_per_second",
        "median_latency_seconds",
        "p95_latency_seconds",
        "cost_per_task_usd",
        "memory_bytes",
    }
)


@dataclass(frozen=True)
class CohortFilter:
    """One side of a comparison: the models a cohort is drawn from.

    Criteria combine with AND; at least one must be declared. ``name_globs``
    match the configured model name, ``names`` is an explicit allow-list,
    ``inferencer`` matches the model's declared engine, and ``quant`` is a
    quant token (``q4``, ``4bit``) matched on a token boundary against the
    configuration's quant or its model name.
    """

    name: str
    name_globs: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    inferencer: str | None = None
    quant: str | None = None

    def matches(
        self,
        model_name: str,
        *,
        inferencer: str | None = None,
        quant: str | None = None,
    ) -> bool:
        """Whether a model belongs to this cohort; every set criterion must hold."""

        if self.names and model_name not in self.names:
            return False
        if self.name_globs and not any(
            fnmatchcase(model_name.lower(), glob.lower()) for glob in self.name_globs
        ):
            return False
        if self.inferencer is not None and inferencer != self.inferencer:
            return False
        if self.quant is not None and not (
            (quant is not None and _has_quant_token(quant, self.quant))
            or _has_quant_token(model_name, self.quant)
        ):
            return False
        return True


def _has_quant_token(text: str, token: str) -> bool:
    """Whether ``token`` appears in ``text`` on a token boundary (``q4`` in ``Q4_K_M``,
    ``4bit`` in ``...-4bit`` — but never ``4bit`` inside ``14bit``)."""

    return re.search(rf"(?<![a-z0-9]){re.escape(token.lower())}(?![a-z0-9])", text.lower()) is not None


@dataclass(frozen=True)
class HighlightedPair:
    """A controlled comparison the axis calls out, with the reason it is clean."""

    models: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class VerdictRule:
    """A deterministic conclusion rule: a thresholded metric over cohort aggregates.

    ``settings_key`` names the Epic-15 settings entry that overrides
    ``threshold`` once the settings layer lands; until then the shipped
    threshold is the default (story 17.1-002 technical notes).
    """

    id: str
    metric: str
    threshold: float
    unit: str | None = None
    description: str | None = None
    settings_key: str | None = None


@dataclass(frozen=True)
class ComparisonAxis:
    """One declared comparison: cohort filters, pairing key, pairs, verdicts."""

    id: str
    title: str
    pairing_key: PairingKey
    cohorts: tuple[CohortFilter, ...]
    description: str | None = None
    highlighted_pairs: tuple[HighlightedPair, ...] = ()
    verdicts: tuple[VerdictRule, ...] = ()


@dataclass(frozen=True)
class ComparisonCatalog:
    """The loaded catalog: valid axes plus the errors for any rejected ones."""

    axes: tuple[ComparisonAxis, ...]
    errors: tuple[str, ...] = ()

    def axis(self, axis_id: str) -> ComparisonAxis | None:
        """The axis with ``axis_id``, or ``None`` when not declared (or rejected)."""

        return next((axis for axis in self.axes if axis.id == axis_id), None)


def load_comparisons(path: str | Path) -> ComparisonCatalog:
    """Load the comparison-axis catalog from YAML.

    File-level failures (missing file, invalid YAML, wrong top-level shape)
    raise :class:`ConfigError` like the other loaders. A malformed *axis* is
    instead rejected individually — its error names the offending field and is
    collected on :attr:`ComparisonCatalog.errors` — so valid axes still load.
    """

    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"comparison config not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("comparisons.yaml must contain a top-level mapping")
    entries = raw.get("comparisons")
    if not isinstance(entries, list):
        raise ConfigError("comparisons.yaml field 'comparisons' must be a list")

    axes: list[ComparisonAxis] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        try:
            axis = _parse_comparison_axis(entry, index)
            if axis.id in seen:
                raise ConfigError(f"comparisons[{index}].id duplicates '{axis.id}'")
        except ConfigError as exc:
            errors.append(str(exc))
            continue
        seen.add(axis.id)
        axes.append(axis)
    return ComparisonCatalog(axes=tuple(axes), errors=tuple(errors))


def cohort_model_names(
    cohort: CohortFilter, models: Mapping[str, ModelConfig]
) -> tuple[str, ...]:
    """Configured model names a cohort filter selects, in registry order.

    This is what populates an axis's "no comparable runs yet" state: the models
    (and thereby suites to run) that would fill each side once results exist.
    """

    return tuple(
        model.name
        for model in models.values()
        if cohort.matches(model.name, inferencer=model.inferencer, quant=model.quant)
    )


def _parse_comparison_axis(entry: Any, index: int) -> ComparisonAxis:
    if not isinstance(entry, dict):
        raise ConfigError(f"comparisons[{index}] must be a mapping")

    pairing_key = _required_str(entry, "pairing_key", index, root="comparisons")
    if pairing_key not in PAIRING_KEYS:
        allowed = " | ".join(sorted(PAIRING_KEYS))
        raise ConfigError(f"comparisons[{index}].pairing_key must be one of: {allowed}")

    return ComparisonAxis(
        id=_required_str(entry, "id", index, root="comparisons"),
        title=_required_str(entry, "title", index, root="comparisons"),
        pairing_key=pairing_key,  # type: ignore[arg-type]
        cohorts=_parse_cohorts(entry.get("cohorts"), index),
        description=_optional_str(entry, "description", index, root="comparisons"),
        highlighted_pairs=_parse_highlighted_pairs(entry.get("highlighted_pairs"), index),
        verdicts=_parse_verdicts(entry.get("verdicts"), index),
    )


def _parse_cohorts(value: Any, index: int) -> tuple[CohortFilter, ...]:
    if not isinstance(value, list) or len(value) < 2:
        raise ConfigError(f"comparisons[{index}].cohorts must be a list of two or more cohorts")
    root = f"comparisons[{index}].cohorts"
    cohorts: list[CohortFilter] = []
    for position, item in enumerate(value):
        if not isinstance(item, dict):
            raise ConfigError(f"{root}[{position}] must be a mapping")
        cohort = CohortFilter(
            name=_required_str(item, "name", position, root=root),
            name_globs=_optional_str_tuple(item, "name_globs", position, root=root),
            names=_optional_str_tuple(item, "names", position, root=root),
            inferencer=_optional_str(item, "inferencer", position, root=root),
            quant=_optional_str(item, "quant", position, root=root),
        )
        if not (cohort.name_globs or cohort.names or cohort.inferencer or cohort.quant):
            raise ConfigError(
                f"{root}[{position}] must declare at least one of"
                " name_globs/names/inferencer/quant"
            )
        if any(cohort.name == other.name for other in cohorts):
            raise ConfigError(f"{root}[{position}].name duplicates '{cohort.name}'")
        cohorts.append(cohort)
    return tuple(cohorts)


def _parse_highlighted_pairs(value: Any, index: int) -> tuple[HighlightedPair, ...]:
    if value is None:
        return ()
    root = f"comparisons[{index}].highlighted_pairs"
    if not isinstance(value, list):
        raise ConfigError(f"{root} must be a list when set")
    pairs: list[HighlightedPair] = []
    for position, item in enumerate(value):
        if not isinstance(item, dict):
            raise ConfigError(f"{root}[{position}] must be a mapping")
        models = _optional_str_tuple(item, "models", position, root=root)
        if len(models) < 2:
            raise ConfigError(f"{root}[{position}].models must list at least two model names")
        pairs.append(
            HighlightedPair(
                models=models,
                reason=_required_str(item, "reason", position, root=root),
            )
        )
    return tuple(pairs)


def _parse_verdicts(value: Any, index: int) -> tuple[VerdictRule, ...]:
    if value is None:
        return ()
    root = f"comparisons[{index}].verdicts"
    if not isinstance(value, list):
        raise ConfigError(f"{root} must be a list when set")
    verdicts: list[VerdictRule] = []
    for position, item in enumerate(value):
        if not isinstance(item, dict):
            raise ConfigError(f"{root}[{position}] must be a mapping")
        metric = _required_str(item, "metric", position, root=root)
        if metric not in VERDICT_METRICS:
            allowed = " | ".join(sorted(VERDICT_METRICS))
            raise ConfigError(f"{root}[{position}].metric must be one of: {allowed}")
        threshold = item.get("threshold")
        if isinstance(threshold, bool) or not isinstance(threshold, int | float):
            raise ConfigError(f"{root}[{position}].threshold must be a number")
        verdict = VerdictRule(
            id=_required_str(item, "id", position, root=root),
            metric=metric,
            threshold=float(threshold),
            unit=_optional_str(item, "unit", position, root=root),
            description=_optional_str(item, "description", position, root=root),
            settings_key=_optional_str(item, "settings_key", position, root=root),
        )
        if any(verdict.id == other.id for other in verdicts):
            raise ConfigError(f"{root}[{position}].id duplicates '{verdict.id}'")
        verdicts.append(verdict)
    return tuple(verdicts)


def _optional_str_tuple(
    entry: dict[str, Any],
    field: str,
    index: int,
    *,
    root: str,
) -> tuple[str, ...]:
    value = entry.get(field)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ConfigError(f"{root}[{index}].{field} must be a list of non-empty strings when set")
    return tuple(value)
