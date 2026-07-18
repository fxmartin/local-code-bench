"""Read-only settings aggregation for the dashboard Settings tab (Story 15.1-001).

Aggregates every harness config surface — models, inferencers, storage tiering,
suites, and agents — into one JSON document the dashboard's Settings tab renders,
so the whole configuration is visible without opening four YAML files. Every group
is loaded independently from its source file at request time: a missing or
malformed file degrades that one group to an inline error while the others render.

Security posture matches the unified dashboard (story 09.6-001): the payload
carries environment-variable *names* plus a set/unset indicator only — resolved
via ``os.environ`` membership — and never a value, a base URL, or an absolute
host path. Protocol-locked values (local ``concurrency``, benchmark
temperature/seed) are flagged so the tab can mark them read-only with a rationale.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .config import (
    AgentConfig,
    AutoTierConfig,
    ConfigError,
    ExternalRepoConfig,
    InferencerConfig,
    ModelConfig,
    load_agents,
    load_autotier,
    load_external_repo,
    load_inferencers,
    load_models,
    resolve_health_url,
)
from . import pdf_export
from . import theme
from .agents import supported_harness_kinds
from .settings import (
    Settings,
    SettingsError,
    load_settings,
    protocol_entries,
    settings_provenance,
)
from .settings_store import content_hash
from .suite_catalog import suite_catalog

#: Why local endpoint concurrency may not be raised (Benchmark Protocol v1).
LOCAL_CONCURRENCY_RATIONALE = (
    "local servers take one request at a time so shared-GPU contention cannot "
    "distort the prefill/decode measurements"
)

#: Why the correctness protocol pins generation randomness.
PROTOCOL_SAMPLING_RATIONALE = (
    "harness-defined: pass@1 correctness is measured at temperature 0 with a "
    "fixed seed so runs stay reproducible"
)

#: The fixed sampling parameters every benchmark run records (see ``metadata.py``).
PROTOCOL_TEMPERATURE = 0.0
PROTOCOL_SEED = 0

#: Editor note per editable group (Story 15.3-003): what the group's source file
#: does and does not cover, shown next to the edit form.
EDITABLE_GROUP_NOTES = {
    "suites": (
        "built-in suites are code, not config — this file registers custom "
        "suites only (id, label, dataset source, format)"
    ),
    "agents": (
        "entries configure the harness command, workspace sandbox policy, and "
        "timeouts; the invocation shape per harness type is fixed in code"
    ),
    "settings": (
        "operational defaults incl. the dashboard theme; dark-mode tints are "
        "derived from the configured hues, and a poor-contrast hue warns on "
        "save but never blocks"
    ),
}

#: Hosts that identify a locally served endpoint (protocol-locked concurrency).
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def settings_payload(
    *,
    models_path: str | Path = "configs/models.yaml",
    inferencers_path: str | Path = "configs/inferencers.yaml",
    agents_path: str | Path = "configs/agents.yaml",
    suites_path: str | Path = "configs/suites.yaml",
    settings_path: str | Path = "configs/settings.yaml",
    cache_dir: str | Path = ".cache/benchmarks",
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Aggregate every config surface into one JSON-safe settings document.

    Each group re-reads its source file now, so a YAML edit shows up on the next
    refresh and a broken file surfaces as that group's inline ``error`` instead of
    failing the whole document. ``environ`` defaults to ``os.environ`` and is only
    consulted for key *membership* (the set/unset indicator) — except for the
    Harness group's documented env overrides, whose values are operational
    tunables (never secrets) and are shown so an env override is never mistaken
    for the YAML value (story 15.5-002).
    """

    env = os.environ if environ is None else environ
    return {
        "groups": [
            _harness_group(settings_path, env),
            _group(
                "models",
                "Models",
                models_path,
                lambda: _model_items(load_models(models_path), env),
            ),
            _group(
                "inferencers",
                "Inferencers",
                inferencers_path,
                lambda: _inferencer_items(load_inferencers(inferencers_path)),
            ),
            _group(
                "storage",
                "Storage",
                inferencers_path,
                lambda: _storage_items(inferencers_path),
            ),
            _group(
                "suites",
                "Suites",
                suites_path,
                lambda: _suite_items(suites_path, cache_dir),
            ),
            _group(
                "agents",
                "Agents",
                agents_path,
                lambda: _agent_items(load_agents(agents_path), env),
            ),
            _group(
                "settings",
                "Harness",
                settings_path,
                lambda: _theme_items(load_settings(settings_path)),
            ),
        ]
    }


#: Why each protocol key in the Harness group is read-only (story 15.5-002).
_PROTOCOL_RATIONALES = {
    "benchmark_temperature": PROTOCOL_SAMPLING_RATIONALE,
    "benchmark_seed": PROTOCOL_SAMPLING_RATIONALE,
    "local_concurrency": LOCAL_CONCURRENCY_RATIONALE,
}


def _harness_group(settings_path: str | Path, environ: Mapping[str, str]) -> dict[str, Any]:
    """The Harness defaults group: every ``configs/settings.yaml`` key with its
    resolved effective value and source layer (story 15.5-002).

    Carries the file's ``content_hash`` so an edit can run the 15.2-001
    conflict check; ``editable`` is false when the file is missing (the keys
    still render at their fallback layer, there is just nothing to edit).
    """

    path = Path(settings_path)
    error: str | None = None
    try:
        items = _harness_items(settings_path, environ)
        hash_value = content_hash(path.read_text(encoding="utf-8")) if path.exists() else None
    except (SettingsError, OSError) as exc:
        items, hash_value, error = [], None, str(exc)
    return {
        "id": "harness",
        "label": "Harness",
        "source": str(settings_path),
        "error": error,
        "items": items,
        "editable": hash_value is not None,
        "content_hash": hash_value,
    }


def _harness_items(settings_path: str | Path, environ: Mapping[str, str]) -> list[dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for entry in settings_provenance(settings_path, environ):
        field: dict[str, Any] = {
            "label": entry.key,
            "value": entry.value,
            "source": entry.layer,
            "key": f"{entry.section}.{entry.key}",
            "editable": True,
            "yaml_value": entry.yaml_value,
        }
        if entry.env_var is not None:
            field["env_var"] = entry.env_var
            field["env_active"] = entry.env_active
        note = _harness_note(entry)
        if note:
            field["note"] = note
        items.setdefault(entry.section, {"name": entry.section, "fields": []})
        items[entry.section]["fields"].append(field)
    protocol_fields = [
        _field(key, value, locked=True, rationale=_PROTOCOL_RATIONALES[key])
        for key, value in protocol_entries().items()
    ]
    return [*items.values(), {"name": "protocol", "fields": protocol_fields}]


def _harness_note(entry: Any) -> str:
    """Server-side layering note, so the tab renders without interpreting."""

    parts = []
    if entry.env_active:
        parts.append(f"env {entry.env_var} wins until unset — an edit here will not take effect")
    elif entry.env_var is not None:
        parts.append(f"env {entry.env_var} overrides when set")
    if entry.flag is not None:
        parts.append(f"CLI flag {entry.flag} overrides per run")
    return "; ".join(parts)


def _group(
    group_id: str,
    label: str,
    source: str | Path,
    build: Callable[[], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Build one settings group, degrading load failures to an inline error.

    ``content_hash`` is the source file's poll token for external-change
    detection (story 15.4-001): the tab compares it across refreshes and flags
    the group "changed on disk — reload" on a mismatch. It is present even when
    the loader rejects the file (an out-of-band edit can break a group) and
    ``None`` only when the file cannot be read at all.
    """

    group: dict[str, Any] = {
        "id": group_id,
        "label": label,
        "source": str(source),
        "content_hash": _source_hash(source),
        "error": None,
        "items": [],
        # Story 15.3-003: groups whose source file has a dashboard editor. The
        # group id doubles as the settings-store config id the editor targets.
        "editable": group_id in EDITABLE_GROUP_NOTES,
        "editable_note": EDITABLE_GROUP_NOTES.get(group_id),
    }
    try:
        group["items"] = build()
    except (ConfigError, SettingsError, OSError) as exc:
        group["error"] = str(exc)
    return group


def _source_hash(source: str | Path) -> str | None:
    try:
        return content_hash(Path(source).read_text(encoding="utf-8"))
    except OSError:
        return None


def _field(
    label: str,
    value: object,
    *,
    locked: bool = False,
    rationale: str | None = None,
) -> dict[str, Any]:
    field: dict[str, Any] = {"label": label, "value": value}
    if locked:
        field["locked"] = True
        field["rationale"] = rationale
    return field


def _env_field(label: str, env_name: str, environ: Mapping[str, str]) -> dict[str, Any]:
    """An env-var reference: the variable *name* plus set/unset, never the value."""

    return {"label": label, "value": env_name, "is_set": env_name in environ}


def _is_local_model(cfg: ModelConfig) -> bool:
    """Whether this endpoint is served on this box (concurrency protocol-locked)."""

    if cfg.inferencer is not None:
        return True
    host = cfg.base_url.split("//", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
    return host in _LOCAL_HOSTS


def _model_items(
    models: dict[str, ModelConfig], environ: Mapping[str, str]
) -> list[dict[str, Any]]:
    items = []
    for cfg in models.values():
        local = _is_local_model(cfg)
        fields = [
            _field("type", cfg.type),
            _field("model id", cfg.model_id),
            _field("pinned revision", cfg.pinned_revision),
            _field(
                "concurrency",
                cfg.concurrency,
                locked=local,
                rationale=LOCAL_CONCURRENCY_RATIONALE if local else None,
            ),
            _field(
                "max tokens", cfg.max_tokens if cfg.max_tokens is not None else "default (1024)"
            ),
            _field("price / 1k input tokens", cfg.price_per_1k_tokens.input),
            _field("price / 1k output tokens", cfg.price_per_1k_tokens.output),
        ]
        if cfg.api_key_env is not None:
            fields.append(_env_field("API key env", cfg.api_key_env, environ))
        for label, value in (
            ("inferencer", cfg.inferencer),
            ("quant", cfg.quant),
            ("provider", cfg.provider),
            ("engine", cfg.engine),
        ):
            if value is not None:
                fields.append(_field(label, value))
        items.append({"name": cfg.name, "fields": fields})
    return items


def _inferencer_items(inferencers: dict[str, InferencerConfig]) -> list[dict[str, Any]]:
    items = []
    for cfg in inferencers.values():
        fields = [
            _field("lifecycle", cfg.lifecycle),
            _field("detect", f"{cfg.detect_kind}: {cfg.detect_target}"),
            _field("port", cfg.port),
            _field("health probe", resolve_health_url(cfg)),
        ]
        if cfg.start is not None:
            fields.append(_field("start command", " ".join(cfg.start)))
        if cfg.stop is not None:
            fields.append(_field("stop command", " ".join(cfg.stop)))
        if cfg.url is not None:
            fields.append(_field("reference", cfg.url))
        items.append({"name": cfg.name, "fields": fields})
    return items


def _storage_items(inferencers_path: str | Path) -> list[dict[str, Any]]:
    """Local per-engine model stores plus the optional Epic-12 tier blocks."""

    inferencers = load_inferencers(inferencers_path)
    external = load_external_repo(inferencers_path)
    auto_tier = load_autotier(inferencers_path)
    items = []
    for cfg in inferencers.values():
        if cfg.model_store is None:
            continue
        items.append(
            {
                "name": f"{cfg.name} local store",
                "fields": [
                    _field("format", cfg.store_format),
                    _field("paths", ", ".join(cfg.model_store)),
                ],
            }
        )
    items.append({"name": "external_repo", "fields": _external_repo_fields(external)})
    items.append({"name": "auto_tier", "fields": _auto_tier_fields(auto_tier)})
    return items


def _external_repo_fields(external: ExternalRepoConfig | None) -> list[dict[str, Any]]:
    if external is None:
        return [_field("status", "not configured")]
    subpaths = ", ".join(f"{fmt}: {sub}" for fmt, sub in sorted(external.subpaths.items()))
    return [
        _field("root", external.root),
        _field("volume marker", external.volume_marker),
        _field("subpaths", subpaths),
    ]


def _auto_tier_fields(auto_tier: AutoTierConfig | None) -> list[dict[str, Any]]:
    if auto_tier is None:
        return [_field("status", "not configured")]
    return [
        _field("max local GiB", auto_tier.max_local_gb),
        _field("min free GiB", auto_tier.min_free_gb),
        _field("pinned models", ", ".join(auto_tier.pins) or "(none)"),
    ]


def _suite_items(suites_path: str | Path, cache_dir: str | Path) -> list[dict[str, Any]]:
    """The availability-aware suite catalog plus the fixed benchmark protocol."""

    items = []
    for entry in suite_catalog(cache_dir=cache_dir, suites_path=suites_path):
        fields = [
            _field("label", entry.label),
            _field("kind", entry.kind),
            _field("available", "yes" if entry.available else f"no — {entry.reason}"),
        ]
        if entry.task_count is not None:
            fields.append(_field("tasks", entry.task_count))
        if entry.source is not None:
            fields.append(_field("dataset", entry.source))
        items.append({"name": entry.id, "fields": fields})
    items.append(
        {
            "name": "benchmark protocol",
            "fields": [
                _field(
                    "temperature",
                    PROTOCOL_TEMPERATURE,
                    locked=True,
                    rationale=PROTOCOL_SAMPLING_RATIONALE,
                ),
                _field("seed", PROTOCOL_SEED, locked=True, rationale=PROTOCOL_SAMPLING_RATIONALE),
            ],
        }
    )
    return items


def _theme_items(settings: Settings) -> list[dict[str, Any]]:
    """The Harness/theme group (story 16.4-001): configured hues, derived tints.

    The dark tints are shown but not editable as values of their own — they are
    always derived from the configured hues, keeping one hue per role.

    The PDF-export item (story 17.3-002) shows the live renderer detection
    next to the configured candidates, so when one-click Download PDF is
    unavailable the tab names exactly which binary would enable it.
    """

    renderer = pdf_export.detect_renderer(settings.pdf_renderer_candidates)
    return [
        {
            "name": "theme",
            "fields": [
                _field("accent", settings.theme_accent),
                _field("accent dark tint (derived)", theme.dark_tint(settings.theme_accent)),
                _field("danger", settings.theme_danger),
                _field("danger dark tint (derived)", theme.dark_tint(settings.theme_danger)),
                _field("default mode", settings.theme_default_mode),
            ],
        },
        {
            "name": "pdf export",
            "fields": [
                _field(
                    "renderer detected",
                    renderer.candidate
                    if renderer is not None
                    else "none — one-click export disabled",
                ),
                _field("renderer candidates", ", ".join(settings.pdf_renderer_candidates)),
                _field("render timeout seconds", settings.pdf_render_timeout_seconds),
            ],
        },
    ]


def _agent_items(
    agents: dict[str, AgentConfig], environ: Mapping[str, str]
) -> list[dict[str, Any]]:
    items = []
    supported = ", ".join(supported_harness_kinds()) or "(none)"
    type_rationale = (
        "the agent runner treats the harness type as fixed — adapters are code; "
        f"supported types: {supported}"
    )
    for cfg in agents.values():
        fields = [
            _field("type", cfg.type, locked=True, rationale=type_rationale),
            _field("command", cfg.command),
            _field("sandbox", cfg.sandbox),
            _field("timeout seconds", cfg.timeout_seconds),
        ]
        for label, value in (
            ("model", cfg.model),
            ("profile", cfg.profile),
            ("inferencer", cfg.inferencer),
            ("reference", cfg.url),
            ("system prompt", cfg.system_prompt),
            ("append system prompt", cfg.append_system_prompt),
        ):
            if value is not None:
                fields.append(_field(label, value))
        if cfg.api_key_env is not None:
            fields.append(_env_field("API key env", cfg.api_key_env, environ))
        if cfg.anthropic_api_key_env is not None:
            fields.append(_env_field("Anthropic API key env", cfg.anthropic_api_key_env, environ))
        items.append({"name": cfg.name, "fields": fields})
    return items
