"""Inferencers & storage editor: editable document + guarded writes (Story 15.3-002).

The Settings tab's editable surface for ``inferencers.yaml``: per-engine
``model_store`` paths and on-disk format, the optional ``external_repo`` block,
and the ``auto_tier`` policy. Lifecycle, detection, port, and start/stop argv
are install facts, not preferences — the payload carries them display-only and
:func:`apply_edit` rejects any update outside the editable set, so the
dashboard can never rewrite how an engine is launched.

Writes ride the 15.2-001 pipeline (:class:`~.settings_store.SettingsStore`):
conflict-checked against the submitted content hash, validated by the
harness's own loaders, and written atomically with a backup. Path checks warn
but never block — a store path or external root that does not exist right now
is a normal state (an unplugged SSD), so the warning is advisory and the tier
simply reads offline until the volume returns. Running engines (Epic-08 state)
are flagged ``restart_pending``: the edit lands in the file immediately but
the live process keeps its startup configuration until its next start.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

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
