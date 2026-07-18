"""Shared loader for operational defaults (``configs/settings.yaml``).

One resolution order for every tunable, per story 15.5-001:

    CLI flag  >  env var  >  configs/settings.yaml  >  built-in fallback

This module implements the bottom two layers (plus the one documented env
override, ``BENCH_PROVIDER_TIMEOUT_SECONDS``, which its consumer in
:mod:`local_code_bench.provider` reads at call time). CLI flags win simply by
being passed explicitly — argparse defaults are seeded from :func:`get_settings`.

The file is additive: when it is absent or a key is missing, the built-in
fallbacks apply and behaviour is identical to a checkout without the file. The
shipped ``configs/settings.yaml`` intentionally equals the fallbacks
(``tests/test_settings.py`` locks that invariant).

Measurement-protocol values (benchmark temperature/seed, local-model
concurrency) are NOT tunable here: the ``protocol:`` section is read-only and
the loader rejects any value that differs from the locked constants, so the
settings file cannot become a side door around the protocol. The canary anchor
set (``tasks.CANARY_HUMANEVAL_IDS``) is protocol-locked too and deliberately
has no settings key at all. See ``docs/SETTINGS.md`` for the full audit
inventory.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .theme import DEFAULT_ACCENT, DEFAULT_DANGER, DEFAULT_MODE, THEME_MODES, ThemeConfig

DEFAULT_SETTINGS_PATH = Path("configs/settings.yaml")

# Env layer of endpoint.provider_timeout_seconds, parsed by provider.py at
# request time so shell overrides keep working exactly as before 15.5-001.
PROVIDER_TIMEOUT_ENV = "BENCH_PROVIDER_TIMEOUT_SECONDS"


class SettingsError(ValueError):
    """Raised when configs/settings.yaml is malformed or overrides protocol."""


@dataclass(frozen=True)
class Settings:
    """Typed operational defaults; field defaults are the built-in fallbacks."""

    endpoint_max_tokens: int = 1024
    provider_timeout_seconds: float = 120.0
    chat_temperature: float = 0.7
    chat_max_tokens: int = 1024
    sandbox_timeout_seconds: float = 5.0
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8770
    unified_dashboard_port: int = 8765
    dashboard_state_file: str = ".runtime/dashboard.json"
    cache_dir: str = ".cache/benchmarks"
    results_dir: str = "results"
    inferencer_state_dir: str = ".runtime/inferencers"
    optimizer_state_dir: str = ".runtime/optimizers"
    inferencer_start_timeout_seconds: float = 30.0
    inferencer_health_timeout_seconds: float = 1.0
    opencode_build_timeout_seconds: float = 60.0
    opencode_run_timeout_seconds: float = 10.0
    settings_backup_dir: str = ".runtime/settings-backups"
    settings_backup_retention: int = 10
    # PDF export (story 17.3-002): Chrome/Chromium probed in order for the
    # dashboard's one-click Download PDF — detect-only, never installed. Bare
    # names resolve via PATH; entries with a slash are ``.app``-relative paths
    # probed under the macOS Application directories.
    pdf_renderer_candidates: tuple[str, ...] = (
        "google-chrome",
        "chromium",
        "Google Chrome.app/Contents/MacOS/Google Chrome",
        "Chromium.app/Contents/MacOS/Chromium",
    )
    pdf_render_timeout_seconds: float = 60.0
    # Dashboard theme (story 16.4-001): light-mode hues (#RRGGBB) and the
    # initial mode; dark-mode tints are derived by the theme layer, never set.
    theme_accent: str = DEFAULT_ACCENT
    theme_danger: str = DEFAULT_DANGER
    theme_default_mode: str = DEFAULT_MODE


# YAML (section, key) -> Settings field. The YAML stays sectioned for humans;
# the dataclass stays flat for call sites.
_KEY_MAP: dict[tuple[str, str], str] = {
    ("endpoint", "max_tokens"): "endpoint_max_tokens",
    ("endpoint", "provider_timeout_seconds"): "provider_timeout_seconds",
    ("chat", "temperature"): "chat_temperature",
    ("chat", "max_tokens"): "chat_max_tokens",
    ("sandbox", "timeout_seconds"): "sandbox_timeout_seconds",
    ("dashboard", "host"): "dashboard_host",
    ("dashboard", "port"): "dashboard_port",
    ("dashboard", "unified_port"): "unified_dashboard_port",
    ("dashboard", "state_file"): "dashboard_state_file",
    ("paths", "cache_dir"): "cache_dir",
    ("paths", "results_dir"): "results_dir",
    ("paths", "inferencer_state_dir"): "inferencer_state_dir",
    ("paths", "optimizer_state_dir"): "optimizer_state_dir",
    ("inferencer", "start_timeout_seconds"): "inferencer_start_timeout_seconds",
    ("inferencer", "health_timeout_seconds"): "inferencer_health_timeout_seconds",
    ("opencode", "build_timeout_seconds"): "opencode_build_timeout_seconds",
    ("opencode", "run_timeout_seconds"): "opencode_run_timeout_seconds",
    ("settings_backup", "dir"): "settings_backup_dir",
    ("settings_backup", "retention"): "settings_backup_retention",
    ("pdf", "renderer_candidates"): "pdf_renderer_candidates",
    ("pdf", "render_timeout_seconds"): "pdf_render_timeout_seconds",
    ("theme", "accent"): "theme_accent",
    ("theme", "danger"): "theme_danger",
    ("theme", "default_mode"): "theme_default_mode",
}

# Fields whose values must be strictly positive (timeouts, caps, ports, counts).
_POSITIVE_FIELDS = {
    "endpoint_max_tokens",
    "provider_timeout_seconds",
    "chat_max_tokens",
    "sandbox_timeout_seconds",
    "dashboard_port",
    "unified_dashboard_port",
    "inferencer_start_timeout_seconds",
    "inferencer_health_timeout_seconds",
    "opencode_build_timeout_seconds",
    "opencode_run_timeout_seconds",
    "settings_backup_retention",
    "pdf_render_timeout_seconds",
}

# Theme hues must be full #RRGGBB hex so the token block and the luminance
# math never see a malformed color (story 16.4-001).
_HEX_COLOR_FIELDS = {"theme_accent", "theme_danger"}
_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}\Z")

# Read-only measurement protocol: present in the shipped file for visibility,
# but any deviation from these locked values is refused. The canary anchor set
# is also protocol-locked and has no key here on purpose.
_PROTOCOL_LOCKED: dict[str, float | int] = {
    "benchmark_temperature": 0.0,
    "benchmark_seed": 0,
    "local_concurrency": 1,
}

_FIELD_TYPES = {field.name: field.type for field in fields(Settings)}

# Documented env-var layer per field (env > yaml > fallback). Today only the
# provider timeout has one; extend here when a new override is documented.
_ENV_OVERRIDES: dict[str, str] = {
    "provider_timeout_seconds": PROVIDER_TIMEOUT_ENV,
}

# Documented CLI-flag layer per field — display metadata for the Settings tab.
# Flags are per-invocation, so the loader can never resolve them as the source
# of an effective value; the tab shows them as the "overrides per run" layer.
_CLI_FLAGS: dict[str, str] = {
    "endpoint_max_tokens": "--max-tokens",
    "sandbox_timeout_seconds": "--timeout",
    "dashboard_host": "--host",
    "dashboard_port": "--port",
    "unified_dashboard_port": "--port",
    "dashboard_state_file": "--state-file",
    "cache_dir": "--cache-dir",
    "results_dir": "--results-dir",
    "inferencer_state_dir": "--state-dir",
    "optimizer_state_dir": "--optimizer-state-dir",
}


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings from ``path`` (default ``configs/settings.yaml``).

    A missing file yields the built-in fallbacks unchanged; a malformed file,
    an unknown key, or a protocol override raises :class:`SettingsError`.
    """

    settings_path = Path(path) if path is not None else DEFAULT_SETTINGS_PATH
    return Settings(**_load_overrides(settings_path))


def _load_overrides(settings_path: Path) -> dict[str, Any]:
    """The validated yaml layer as ``Settings`` field overrides (may be empty)."""

    if not settings_path.exists():
        return {}

    try:
        raw = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SettingsError(f"{settings_path}: invalid YAML: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SettingsError(f"{settings_path}: top level must be a mapping of sections")

    overrides: dict[str, Any] = {}
    for section, entries in raw.items():
        if section == "protocol":
            _check_protocol(settings_path, entries)
            continue
        if not isinstance(entries, dict):
            raise SettingsError(f"{settings_path}: section '{section}' must be a mapping")
        for key, value in entries.items():
            field_name = _KEY_MAP.get((section, key))
            if field_name is None:
                raise SettingsError(f"{settings_path}: unknown setting '{section}.{key}'")
            overrides[field_name] = _coerce(settings_path, f"{section}.{key}", field_name, value)
    return overrides


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings from the default path, resolved once per process."""

    return load_settings(DEFAULT_SETTINGS_PATH)


def theme_config(settings: Settings) -> ThemeConfig:
    """The theme layer's view of loaded settings (story 16.4-001)."""

    return ThemeConfig(
        accent=settings.theme_accent,
        danger=settings.theme_danger,
        default_mode=settings.theme_default_mode,
    )


def load_theme_config(path: str | Path | None = None) -> ThemeConfig:
    """Fresh theme config for the render path — never a broken theme.

    Re-reads the settings file per call so a saved edit shows on the next page
    refresh without a restart. A malformed file falls back to the shipped
    defaults here; the loader error itself still surfaces through the CLI and
    the Settings tab's validated write path, which refuses to save it.
    """

    try:
        return theme_config(load_settings(path))
    except SettingsError:
        return ThemeConfig()


@dataclass(frozen=True)
class SettingProvenance:
    """One settings key with its effective value and the layer that produced it.

    ``layer`` is the highest layer the loader can resolve at read time —
    ``"env"``, ``"yaml"``, or ``"fallback"``. The CLI-flag layer is
    per-invocation and therefore never the resolved source here; where a
    documented flag exists it is exposed as ``flag`` display metadata.
    """

    section: str
    key: str
    field: str
    value: object
    yaml_value: object | None
    layer: str
    env_var: str | None
    env_active: bool
    flag: str | None


def settings_provenance(
    path: str | Path | None = None, environ: Mapping[str, str] | None = None
) -> list[SettingProvenance]:
    """Per-key provenance for the Settings tab (story 15.5-002).

    Resolves env > yaml > fallback for every key in :data:`_KEY_MAP`, keeping
    the yaml layer visible even when an env override wins so the tab can state
    that an edit to the file will not take effect until the variable is unset.
    A malformed file raises :class:`SettingsError` (the caller degrades it to
    an inline group error like every other config surface).
    """

    env = os.environ if environ is None else environ
    settings_path = Path(path) if path is not None else DEFAULT_SETTINGS_PATH
    overrides = _load_overrides(settings_path)
    fallbacks = Settings()

    entries = []
    for (section, key), field_name in _KEY_MAP.items():
        env_var = _ENV_OVERRIDES.get(field_name)
        env_raw = env.get(env_var) if env_var is not None else None
        if env_raw is not None:
            layer, value = "env", _display_env_value(env_raw)
        elif field_name in overrides:
            layer, value = "yaml", overrides[field_name]
        else:
            layer, value = "fallback", getattr(fallbacks, field_name)
        entries.append(
            SettingProvenance(
                section=section,
                key=key,
                field=field_name,
                value=value,
                yaml_value=overrides.get(field_name),
                layer=layer,
                env_var=env_var,
                env_active=env_raw is not None,
                flag=_CLI_FLAGS.get(field_name),
            )
        )
    return entries


def protocol_entries() -> dict[str, float | int]:
    """The read-only protocol section (key -> locked value), for display."""

    return dict(_PROTOCOL_LOCKED)


def parse_setting_value(dotted: str, value: object) -> object:
    """Coerce one submitted Harness-group edit to its typed, validated value.

    Accepts the string form a web form submits (``"2048"``) as well as
    already-typed values; unknown keys, protocol keys, wrong types, and
    non-positive numbers raise :class:`SettingsError`.
    """

    section, _, key = dotted.partition(".")
    if section == "protocol":
        raise SettingsError(f"{dotted} is read-only (measurement protocol)")
    field_name = _KEY_MAP.get((section, key))
    if field_name is None:
        raise SettingsError(f"unknown setting '{dotted}'")
    annotation = _FIELD_TYPES[field_name]
    if isinstance(value, str) and annotation in ("int", "float"):
        try:
            value = int(value.strip()) if annotation == "int" else float(value.strip())
        except ValueError:
            kind = "an integer" if annotation == "int" else "a number"
            raise SettingsError(f"{dotted} must be {kind}") from None
    return _coerce(DEFAULT_SETTINGS_PATH, dotted, field_name, value)


def _display_env_value(raw: str) -> object:
    """The env layer's value for display: numeric when parseable, else raw."""

    try:
        return float(raw)
    except ValueError:
        return raw


def _coerce(path: Path, dotted: str, field_name: str, value: object) -> object:
    annotation = _FIELD_TYPES[field_name]
    # bool is an int subclass; a YAML `true` must not sneak in as 1.
    if annotation == "tuple[str, ...]":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise SettingsError(f"{path}: {dotted} must be a list of strings")
        return tuple(value)
    if annotation == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"{path}: {dotted} must be an integer")
        coerced: object = value
    elif annotation == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"{path}: {dotted} must be a number")
        coerced = float(value)
    else:
        if not isinstance(value, str):
            raise SettingsError(f"{path}: {dotted} must be a string")
        coerced = value
    if field_name in _POSITIVE_FIELDS and not (isinstance(coerced, (int, float)) and coerced > 0):
        raise SettingsError(f"{path}: {dotted} must be positive")
    if field_name in _HEX_COLOR_FIELDS and not _HEX_COLOR_RE.fullmatch(str(coerced)):
        raise SettingsError(f"{path}: {dotted} must be a #RRGGBB hex color")
    if field_name == "theme_default_mode" and coerced not in THEME_MODES:
        raise SettingsError(f"{path}: {dotted} must be one of: {', '.join(THEME_MODES)}")
    return coerced


def _check_protocol(path: Path, entries: object) -> None:
    if not isinstance(entries, dict):
        raise SettingsError(f"{path}: section 'protocol' must be a mapping")
    for key, value in entries.items():
        locked = _PROTOCOL_LOCKED.get(key)
        if locked is None:
            raise SettingsError(f"{path}: unknown setting 'protocol.{key}'")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != locked:
            raise SettingsError(
                f"{path}: protocol.{key} is read-only (locked to {locked}); "
                "the settings file cannot override the measurement protocol"
            )
