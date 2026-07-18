"""Suites & agents editor for the dashboard Settings tab (Story 15.3-003).

HTTP-shaped actions over the Story 15.2-001 :class:`~.settings_store.SettingsStore`:
``read_action`` hands the current document (content + conflict hash) to the editor
form, ``write_action`` pushes an edited document back through the store's validated,
atomic, backed-up write path. Only the suites and agents configs are editable here —
the models and inferencers editors are their own stories (15.3-001 / 15.3-002), so
every other id is refused even though the store registers it.

Validation is the harness's own loaders (``load_custom_suites`` / ``load_agents``)
via the store, so the dashboard can never save a config the CLI would reject. A
suites edit that removes (or renames) a custom suite id still referenced by a saved
dashboard launcher selection — a suite recorded in the run history under
``results/`` — is *warned about but allowed*: the write lands and the response
carries the dangling ids, mirroring how the dashboard treats stale history rows.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any

import yaml

from .settings_store import (
    ConflictError,
    SettingsStore,
    SettingsValidationError,
    UnknownConfigError,
    WriteFailedError,
)

#: Config ids this story's editors expose. The store registers more (models,
#: inferencers); those stay read-only until their own editor stories land.
EDITABLE_CONFIG_IDS: tuple[str, ...] = ("suites", "agents")


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
