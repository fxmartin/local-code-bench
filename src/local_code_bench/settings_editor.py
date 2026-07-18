"""Settings-tab editors: inferencers & storage, suites & agents, plus harness
settings.

Three stories share this module, all riding the Story 15.2-001 write pipeline
(:class:`~.settings_store.SettingsStore`): conflict-checked against the
submitted content hash, validated by the harness's own loaders, and written
atomically with a backup.

Story 15.3-002 — inferencers & storage. The Settings tab's editable surface
for ``inferencers.yaml``: per-engine ``model_store`` paths and on-disk format,
the optional ``external_repo`` block, and the ``auto_tier`` policy. Lifecycle,
detection, port, and start/stop argv are install facts, not preferences — the
payload carries them display-only and :func:`apply_edit` rejects any update
outside the editable set, so the dashboard can never rewrite how an engine is
launched. Path checks warn but never block — a store path or external root
that does not exist right now is a normal state (an unplugged SSD), so the
warning is advisory and the tier simply reads offline until the volume
returns. Running engines (Epic-08 state) are flagged ``restart_pending``: the
edit lands in the file immediately but the live process keeps its startup
configuration until its next start.

Story 15.3-003 — suites & agents. HTTP-shaped actions: ``read_action`` hands
the current document (content + conflict hash) to the editor form,
``write_action`` pushes an edited document back through the store's validated,
atomic, backed-up write path. Only the suites and agents configs are editable
there, so every other id is refused even though the store registers it.
Validation is the harness's own loaders (``load_custom_suites`` /
``load_agents``) via the store, so the dashboard can never save a config the
CLI would reject. A suites edit that removes (or renames) a custom suite id
still referenced by a saved dashboard launcher selection — a suite recorded in
the run history under ``results/`` — is *warned about but allowed*: the write
lands and the response carries the dangling ids, mirroring how the dashboard
treats stale history rows.

Story 16.4-001 — harness settings. ``settings.yaml`` (including the dashboard
theme) rides the same ``read_action``/``write_action`` pair, validated by
``load_settings`` via the store. A settings edit whose theme hues fall below
WCAG AA contrast against either mode's background is *warned about but
allowed* — FX owns the final call.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Iterable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

import yaml

from . import theme
from .config import (
    DEFAULT_EXTERNAL_SUBPATHS,
    DEFAULT_VOLUME_MARKER,
    STORE_FORMATS,
    AutoTierConfig,
    ConfigError,
    ExternalRepoConfig,
    InferencerConfig,
    load_autotier,
    load_external_repo,
    load_inferencers,
)
from .inferencers.external import check_availability
from .inferencers.inventory import expand_store_path
from .settings_store import (
    ConflictError,
    SettingsStore,
    SettingsValidationError,
    UnknownConfigError,
    WriteFailedError,
    content_hash,
)

#: The one registered config this editor writes (15.2-001 store id).
CONFIG_ID = "inferencers"

#: Flag carried by a running engine's entry: the edit is written now, but the
#: live process keeps the configuration it was started with (Epic-08 state).
RESTART_NOTE = "engine is running — this change applies from its next start"

#: Dotted update paths the editor may write. Everything else in
#: ``inferencers.yaml`` (lifecycle, detect, port, health_url, start/stop argv)
#: is an install fact edited in the YAML directly, never from the dashboard.
_EDITABLE_PATTERNS = (
    re.compile(r"^inferencers\.\d+\.(model_store|format)$"),
    re.compile(r"^external_repo$"),
    re.compile(r"^external_repo\.(root|volume_marker)$"),
    re.compile(r"^external_repo\.subpaths(\.[^.]+)?$"),
    re.compile(r"^auto_tier$"),
    re.compile(r"^auto_tier\.(max_local_gb|min_free_gb|pins)$"),
)

#: Extracts the engine index from an ``inferencers.<idx>.<field>`` update.
_ENGINE_UPDATE = re.compile(r"^inferencers\.(\d+)\.")


def editor_payload(
    inferencers_path: str | Path,
    *,
    running: Collection[str] = (),
    pin_suggestions: Sequence[str] = (),
    home: Path | None = None,
) -> dict[str, Any]:
    """Build the editable Inferencers & Storage document for the Settings tab.

    Re-reads ``inferencers.yaml`` now, so the document always reflects the file
    on disk; ``content_hash`` is the 15.2-001 conflict token the client echoes
    back with its edit. ``running`` is the set of engine names the Epic-08
    state reports live; ``pin_suggestions`` are current inventory model names
    offered by the pins editor. A missing or malformed file degrades to an
    inline ``error`` (matching the read-only tab) instead of failing.
    """

    path = Path(inferencers_path)
    try:
        raw = path.read_text(encoding="utf-8")
        inferencers = load_inferencers(path)
        external = load_external_repo(path)
        auto_tier = load_autotier(path)
    except (ConfigError, OSError) as exc:
        return {
            "config_id": CONFIG_ID,
            "source": str(path),
            "content_hash": None,
            "error": str(exc),
            "formats": sorted(STORE_FORMATS),
            "engines": [],
            "storage": None,
        }

    running_names = set(running)
    return {
        "config_id": CONFIG_ID,
        "source": str(path),
        "content_hash": content_hash(raw),
        "error": None,
        "formats": sorted(STORE_FORMATS),
        "engines": [
            _engine_entry(index, cfg, cfg.name in running_names, home)
            for index, cfg in enumerate(inferencers.values())
        ],
        "storage": {
            "external_repo": _external_entry(external, home),
            "auto_tier": _auto_tier_entry(auto_tier),
            "pin_suggestions": list(pin_suggestions),
        },
    }


def apply_edit(
    store: SettingsStore,
    body: Any,
    *,
    running: Collection[str] = (),
    home: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """Apply an editor submission through the 15.2-001 write pipeline.

    Guards the editable surface first (an update naming a display-only field
    is rejected before any pipeline work), then delegates to
    :meth:`SettingsStore.apply_updates` — conflict check, loader validation,
    atomic write + backup. A successful write returns advisory ``warnings``
    for configured paths that do not exist yet (warn, never block) and
    ``restart_pending`` naming the running engines whose entries changed.
    """

    if not isinstance(body, dict):
        return 400, {"error": "request body must be a JSON object"}
    expected_hash = body.get("expected_hash")
    updates = body.get("updates")
    if not isinstance(expected_hash, str) or not expected_hash:
        return 400, {"error": "expected_hash must carry the content hash the form loaded"}
    if not isinstance(updates, dict) or not updates:
        return 400, {"error": "updates must be a non-empty object of dotted settings paths"}
    for dotted in updates:
        if not _is_editable(str(dotted)):
            return 400, {
                "error": (
                    f"'{dotted}' is not editable from the dashboard — lifecycle, "
                    "detection, and start commands are install facts; edit "
                    "inferencers.yaml directly"
                )
            }

    try:
        result = store.apply_updates(CONFIG_ID, updates, expected_hash=expected_hash)
    except ConflictError as exc:
        return 409, {"error": str(exc), "current_hash": exc.current_hash}
    except SettingsValidationError as exc:
        return 400, {"error": str(exc)}
    except WriteFailedError as exc:
        return 500, {"error": str(exc)}
    return 200, {
        "ok": True,
        "content_hash": result.content_hash,
        "warnings": _post_write_warnings(result.path, home),
        "restart_pending": _restart_pending(result.path, updates, running),
    }


def _is_editable(dotted: str) -> bool:
    return any(pattern.match(dotted) for pattern in _EDITABLE_PATTERNS)


def _engine_entry(
    index: int, cfg: InferencerConfig, is_running: bool, home: Path | None
) -> dict[str, Any]:
    display: list[dict[str, Any]] = [
        {"label": "lifecycle", "value": cfg.lifecycle},
        {"label": "detect", "value": f"{cfg.detect_kind}: {cfg.detect_target}"},
        {"label": "port", "value": cfg.port},
    ]
    if cfg.start is not None:
        display.append({"label": "start command", "value": " ".join(cfg.start)})
    if cfg.stop is not None:
        display.append({"label": "stop command", "value": " ".join(cfg.stop)})
    return {
        "name": cfg.name,
        "index": index,
        "running": is_running,
        "restart_note": RESTART_NOTE if is_running else None,
        "display": display,
        "store": {
            "configured": cfg.model_store is not None,
            "paths": list(cfg.model_store or ()),
            "format": cfg.store_format,
            "warnings": _store_warnings(cfg, home),
        },
    }


def _store_warnings(cfg: InferencerConfig, home: Path | None) -> list[str]:
    """Advisory notes for configured store paths that do not exist right now."""

    return [
        (
            f"store path {raw} does not currently exist — an unplugged or "
            "not-yet-created store is normal; it is scanned when present"
        )
        for raw in cfg.model_store or ()
        if not expand_store_path(raw, home=home).exists()
    ]


def _external_entry(external: ExternalRepoConfig | None, home: Path | None) -> dict[str, Any]:
    if external is None:
        # Defaults pre-fill the form so configuring the tier is one edit away.
        return {
            "configured": False,
            "root": "",
            "volume_marker": DEFAULT_VOLUME_MARKER,
            "subpaths": dict(DEFAULT_EXTERNAL_SUBPATHS),
            "warnings": [],
        }
    return {
        "configured": True,
        "root": external.root,
        "volume_marker": external.volume_marker,
        "subpaths": dict(external.subpaths),
        "warnings": _external_warnings(external, home),
    }


def _external_warnings(external: ExternalRepoConfig, home: Path | None) -> list[str]:
    """Advisory note when the external root or its volume marker is absent."""

    if check_availability(external, home=home).is_mounted:
        return []
    return [
        (
            f"external root {external.root} is not currently mounted with its "
            f"volume marker ({external.volume_marker}) — an unplugged SSD is a "
            "normal state; the tier reads offline until it returns"
        )
    ]


def _auto_tier_entry(auto_tier: AutoTierConfig | None) -> dict[str, Any]:
    if auto_tier is None:
        return {"configured": False, "max_local_gb": None, "min_free_gb": None, "pins": []}
    return {
        "configured": True,
        "max_local_gb": auto_tier.max_local_gb,
        "min_free_gb": auto_tier.min_free_gb,
        "pins": list(auto_tier.pins),
    }


def _post_write_warnings(path: Path, home: Path | None) -> list[str]:
    """Path warnings recomputed from the freshly written (loader-valid) file."""

    warnings: list[str] = []
    for cfg in load_inferencers(path).values():
        warnings.extend(_store_warnings(cfg, home))
    external = load_external_repo(path)
    if external is not None:
        warnings.extend(_external_warnings(external, home))
    return warnings


def _restart_pending(path: Path, updates: dict[str, Any], running: Collection[str]) -> list[str]:
    """Running engines whose entries were edited — the change waits for a restart."""

    edited = {
        int(match.group(1))
        for dotted in updates
        if (match := _ENGINE_UPDATE.match(str(dotted))) is not None
    }
    if not edited:
        return []
    names = [cfg.name for cfg in load_inferencers(path).values()]
    running_names = set(running)
    return [names[i] for i in sorted(edited) if i < len(names) and names[i] in running_names]


# ---------------------------------------------------------------------------
# Suites & agents editor (Story 15.3-003)
# ---------------------------------------------------------------------------

#: Config ids these stories' editors expose. The store registers more (models,
#: inferencers); those stay read-only until their own editor stories land.
EDITABLE_CONFIG_IDS: tuple[str, ...] = ("suites", "agents", "settings")


def read_action(store: SettingsStore, config_id: str) -> tuple[int, dict[str, Any]]:
    """Load an editable config for the form: content plus its conflict hash."""

    if config_id not in EDITABLE_CONFIG_IDS:
        return 404, {"error": _not_editable(config_id)}
    try:
        document = store.read(config_id)
    except ConflictError as exc:
        return 409, {"error": str(exc)}
    except UnknownConfigError as exc:  # pragma: no cover - editable ids are registered
        return 404, {"error": str(exc)}
    return 200, {
        "config_id": document.config_id,
        "source": document.path.name,
        "content": document.content,
        "content_hash": document.content_hash,
    }


def write_action(
    store: SettingsStore,
    config_id: str,
    body: object,
    *,
    referenced_suites: Callable[[], set[str]] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Write an edited document through the validated store pipeline.

    ``referenced_suites`` supplies the suite ids used by saved launcher
    selections; it is only consulted after a suites edit passes validation, to
    build the warn-but-allow dangling-reference list.
    """

    if config_id not in EDITABLE_CONFIG_IDS:
        return 404, {"error": _not_editable(config_id)}
    if not isinstance(body, dict):
        return 400, {"error": "request body must be a JSON object"}
    content = body.get("content")
    expected_hash = body.get("expected_hash")
    if not isinstance(content, str) or not isinstance(expected_hash, str):
        return 400, {"error": "body must carry string 'content' and 'expected_hash'"}

    try:
        previous = store.read(config_id).content
        result = store.write(config_id, content, expected_hash=expected_hash)
    except ConflictError as exc:
        payload: dict[str, Any] = {"error": str(exc)}
        if exc.current_hash is not None:
            payload["current_hash"] = exc.current_hash
        return 409, payload
    except SettingsValidationError as exc:
        return 422, {"error": str(exc)}
    except WriteFailedError as exc:
        return 500, {"error": str(exc)}
    except UnknownConfigError as exc:  # pragma: no cover - editable ids are registered
        return 404, {"error": str(exc)}

    warnings: list[str] = []
    if config_id == "suites" and referenced_suites is not None:
        warnings = dangling_suite_warnings(previous, content, referenced_suites())
    if config_id == "settings":
        warnings = theme_contrast_warnings(content)
    return 200, {
        "config_id": result.config_id,
        "content_hash": result.content_hash,
        "backup": result.backup_path.name,
        "warnings": warnings,
    }


def dangling_suite_warnings(
    previous_content: str, new_content: str, referenced: set[str]
) -> list[str]:
    """Warn for each custom suite id removed by the edit but still referenced."""

    removed = _custom_suite_ids(previous_content) - _custom_suite_ids(new_content)
    return [
        f"suite '{suite_id}' is still referenced by a saved launcher selection "
        "in the run history; those rows will show a dangling suite id"
        for suite_id in sorted(removed & referenced)
    ]


def theme_contrast_warnings(content: str) -> list[str]:
    """AA contrast warnings for a saved settings document's theme block.

    Runs only after the loader has accepted the edit, so the light parse here
    cannot fail on shape; missing keys fall back to the shipped default hues.
    Advisory by design (story 16.4-001): warn, never block.
    """

    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError:  # pragma: no cover - validated content upstream
        return []
    block = raw.get("theme") if isinstance(raw, dict) else None
    values = block if isinstance(block, dict) else {}
    config = theme.ThemeConfig(
        accent=values.get("accent", theme.DEFAULT_ACCENT),
        danger=values.get("danger", theme.DEFAULT_DANGER),
    )
    return theme.contrast_warnings(config)


def referenced_suite_ids(result_paths: Iterable[str | Path]) -> set[str]:
    """Suite ids recorded by saved runs — the launcher selections to warn about.

    Best-effort by design: a missing file or malformed record contributes
    nothing, since a broken history line must never block a settings edit.
    """

    referenced: set[str] = set()
    for path in result_paths:
        with suppress(OSError):
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                with suppress(json.JSONDecodeError):
                    record = json.loads(line)
                    if isinstance(record, dict) and isinstance(record.get("suite"), str):
                        referenced.add(record["suite"])
    return referenced


def _custom_suite_ids(content: str) -> set[str]:
    """Custom suite ids declared in a suites.yaml document, tolerating bad shapes."""

    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError:
        return set()
    if not isinstance(raw, dict) or not isinstance(raw.get("suites"), list):
        return set()
    return {
        entry["id"]
        for entry in raw["suites"]
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }


def _not_editable(config_id: str) -> str:
    editable = ", ".join(EDITABLE_CONFIG_IDS)
    return f"config '{config_id}' is not editable here — editable configs: {editable}"
