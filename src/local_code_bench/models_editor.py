"""Models editor actions for the Settings tab (Story 15.3-001).

A thin form-support layer over the 15.2-001 :class:`~.settings_store.SettingsStore`
pipeline: it reads ``models.yaml`` for prefill, pre-validates the submitted form
(duplicate names, price numbers, the local-concurrency protocol lock) as UX on top
of — never instead of — the loader validation, then round-trips the document with
``ruamel.yaml`` so comments and key order survive, and hands the result to
:meth:`SettingsStore.write` (conflict check, loader validation, backup, atomic
replace).

The form manages the fields the model schema accepts (:data:`MANAGED_KEYS`); any
other key on an entry (``quant``, ``provider``, ``engine``, ``thinking_extra_body``,
…) is preserved untouched on edit and travels with a duplicate. ``extra_body`` is
edited as a YAML/JSON fragment — it is intentionally open-ended.

Unlike the read-only 15.1-001 aggregate, the editor payload carries the endpoint
URL (the form must edit it; the dashboard binds localhost only). The 09.6-001
sanitize seam drops ``base_url`` and ``api_key_env`` keys wholesale as a
defense-in-depth backstop, so the editor ships them under form-specific names —
``endpoint_url`` and ``key_env`` (the environment-variable *name* only,
never a value) — rather than weakening the seam for every other endpoint.
"""

from __future__ import annotations

import copy
import io
import math
from typing import Any

import yaml
from ruamel.yaml.error import YAMLError

from .settings_panel import LOCAL_CONCURRENCY_RATIONALE
from .settings_store import (
    ConflictError,
    SettingsStore,
    SettingsStoreError,
    SettingsValidationError,
    WriteFailedError,
    _round_trip_yaml,
)

#: The fields the form manages, in the order new entries are written.
MANAGED_KEYS = (
    "name",
    "type",
    "base_url",
    "model_id",
    "pinned_revision",
    "api_key_env",
    "concurrency",
    "max_tokens",
    "extra_body",
    "price_per_1k_tokens",
    "inferencer",
)

#: Managed keys dropped from an entry when the form clears them.
_OPTIONAL_KEYS = ("api_key_env", "max_tokens", "extra_body", "inferencer")

_MODEL_TYPES = ("openai", "anthropic")

#: Hosts that identify a locally served endpoint (mirrors ``settings_panel``).
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def models_editor_payload(store: SettingsStore) -> tuple[int, dict[str, Any]]:
    """The form-prefill document: raw field values per entry plus the conflict hash.

    Returns 422 when ``models.yaml`` cannot be parsed — the editor needs a valid
    document to edit against; the read-only tab already degrades that case inline.
    """

    try:
        doc = store.read("models")
    except SettingsStoreError as exc:
        return 409, {"error": str(exc)}
    try:
        entries = _entries(yaml.safe_load(doc.content))
    except (yaml.YAMLError, SettingsValidationError) as exc:
        return 422, {"error": f"models.yaml cannot be edited until it parses: {exc}"}
    return 200, {
        "content_hash": doc.content_hash,
        "concurrency_rationale": LOCAL_CONCURRENCY_RATIONALE,
        "models": [_form_entry(entry) for entry in entries],
    }


def apply_models_action(store: SettingsStore, action: Any) -> tuple[int, dict[str, Any]]:
    """Apply one add / update / duplicate / remove action through the 15.2 pipeline.

    Every rejection happens before the write pipeline is invoked: 400 for form
    errors (duplicate name, bad price, unlocked local concurrency, missing
    confirmation), 404 for an unknown target, 409 for a stale hash. Only a fully
    pre-validated document reaches :meth:`SettingsStore.write`, which still runs
    the harness loader as the final authority (422 on rejection).
    """

    if not isinstance(action, dict):
        return 400, {"error": "action must be a JSON object"}
    expected_hash = action.get("expected_hash")
    if not isinstance(expected_hash, str) or not expected_hash:
        return 400, {"error": "action must carry the expected_hash of the loaded document"}

    try:
        doc = store.read("models")
    except SettingsStoreError as exc:
        return 409, {"error": str(exc)}
    if doc.content_hash != expected_hash:
        return 409, {
            "error": "models.yaml changed on disk after the form was loaded — "
            "reload and reapply the edit"
        }

    yaml_rt = _round_trip_yaml()
    try:
        document = yaml_rt.load(doc.content)
        entries = _entries(document)
    except (YAMLError, SettingsValidationError) as exc:
        return 422, {"error": f"models.yaml cannot be edited until it parses: {exc}"}

    op = action.get("op")
    if op == "add":
        outcome = _apply_add(entries, action)
    elif op == "update":
        outcome = _apply_update(entries, action)
    elif op == "duplicate":
        outcome = _apply_duplicate(entries, action)
    elif op == "remove":
        outcome = _apply_remove(entries, action)
    else:
        return 400, {"error": f"unknown op '{op}' — expected add, update, duplicate, or remove"}
    if outcome is not None:
        return outcome

    buffer = io.StringIO()
    yaml_rt.dump(document, buffer)
    try:
        store.write("models", buffer.getvalue(), expected_hash=expected_hash)
    except ConflictError as exc:
        return 409, {"error": str(exc)}
    except SettingsValidationError as exc:
        return 422, {"error": str(exc)}
    except WriteFailedError as exc:
        return 500, {"error": str(exc)}
    return models_editor_payload(store)


# ---------------------------------------------------------------------------
# operations (mutate the round-tripped entries in place; return an error or None)
# ---------------------------------------------------------------------------


def _apply_add(entries: list, action: dict) -> tuple[int, dict[str, Any]] | None:
    fields, errors = _validate_form(action.get("entry"))
    if errors:
        return 400, {"error": "; ".join(errors), "errors": errors}
    clash = _name_clash(entries, fields["name"])
    if clash is not None:
        return clash
    entries.append(_build_entry(fields))
    return None


def _apply_update(entries: list, action: dict) -> tuple[int, dict[str, Any]] | None:
    target = action.get("name")
    index = _find(entries, target)
    if index is None:
        return 404, {"error": f"unknown model '{target}'"}
    fields, errors = _validate_form(action.get("entry"))
    if errors:
        return 400, {"error": "; ".join(errors), "errors": errors}
    if fields["name"] != target:
        clash = _name_clash(entries, fields["name"])
        if clash is not None:
            return clash
    _update_entry(entries[index], fields)
    return None


def _apply_duplicate(entries: list, action: dict) -> tuple[int, dict[str, Any]] | None:
    target = action.get("name")
    index = _find(entries, target)
    if index is None:
        return 404, {"error": f"unknown model '{target}'"}
    new_name = action.get("new_name")
    if not isinstance(new_name, str) or not new_name.strip():
        return 400, {"error": "duplicate needs a non-empty new_name"}
    new_name = new_name.strip()
    clash = _name_clash(entries, new_name)
    if clash is not None:
        return clash
    duplicate = copy.deepcopy(entries[index])
    duplicate["name"] = new_name
    entries.insert(index + 1, duplicate)
    return None


def _apply_remove(entries: list, action: dict) -> tuple[int, dict[str, Any]] | None:
    target = action.get("name")
    index = _find(entries, target)
    if index is None:
        return 404, {"error": f"unknown model '{target}'"}
    if action.get("confirm") is not True:
        return 400, {"error": f"removing '{target}' requires explicit confirmation (confirm: true)"}
    del entries[index]
    return None


def _find(entries: list, name: Any) -> int | None:
    for index, entry in enumerate(entries):
        if isinstance(entry, dict) and entry.get("name") == name:
            return index
    return None


def _name_clash(entries: list, name: str) -> tuple[int, dict[str, Any]] | None:
    if _find(entries, name) is not None:
        return 400, {"error": f"model name '{name}' already exists in models.yaml"}
    return None


# ---------------------------------------------------------------------------
# form validation
# ---------------------------------------------------------------------------


def _validate_form(entry: Any) -> tuple[dict[str, Any], list[str]]:
    """Validate one submitted form entry into loader-shaped field values."""

    if not isinstance(entry, dict):
        return {}, ["entry must be a JSON object"]
    errors: list[str] = []
    fields: dict[str, Any] = {}

    # form key -> loader key: endpoint_url survives the sanitize seam; base_url would not
    for form_key, key in (
        ("name", "name"),
        ("type", "type"),
        ("endpoint_url", "base_url"),
        ("model_id", "model_id"),
        ("pinned_revision", "pinned_revision"),
    ):
        value = entry.get(form_key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key} is required")
        else:
            fields[key] = value.strip()
    if "type" in fields and fields["type"] not in _MODEL_TYPES:
        errors.append(f"type must be one of: {', '.join(_MODEL_TYPES)}")

    for key in ("key_env", "inferencer"):
        value = entry.get(key)
        target = "api_key_env" if key == "key_env" else key
        if value is None or (isinstance(value, str) and not value.strip()):
            fields[target] = None
        elif isinstance(value, str):
            fields[target] = value.strip()
        else:
            errors.append(f"{key} must be a string")

    fields["concurrency"] = _positive_int(entry.get("concurrency", 1), "concurrency", errors)
    max_tokens = entry.get("max_tokens")
    fields["max_tokens"] = (
        None if max_tokens is None else _positive_int(max_tokens, "max_tokens", errors)
    )
    fields["extra_body"] = _parse_extra_body(entry.get("extra_body"), errors)
    fields["price_per_1k_tokens"] = {
        "input": _price(entry.get("price_input"), "input", errors),
        "output": _price(entry.get("price_output"), "output", errors),
    }

    if not errors and _is_local(fields) and fields["concurrency"] != 1:
        errors.append(
            f"concurrency is locked at 1 for local models — {LOCAL_CONCURRENCY_RATIONALE}"
        )
    return fields, errors


def _positive_int(value: Any, key: str, errors: list[str]) -> Any:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        errors.append(f"{key} must be a positive integer")
        return None
    return value


def _price(value: Any, key: str, errors: list[str]) -> Any:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value < 0
    ):
        errors.append(f"price_per_1k_tokens.{key} must be a non-negative number")
        return None
    return value


def _parse_extra_body(value: Any, errors: list[str]) -> dict | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        errors.append("extra_body must be a YAML or JSON mapping")
        return None
    try:
        parsed = yaml.safe_load(value)
    except yaml.YAMLError as exc:
        errors.append(f"extra_body is not valid YAML: {exc}")
        return None
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        errors.append("extra_body must be a YAML or JSON mapping")
        return None
    return parsed


def _is_local(fields: dict[str, Any]) -> bool:
    """Whether the submitted entry is served on this box (concurrency locked)."""

    if fields.get("inferencer"):
        return True
    base_url = fields.get("base_url") or ""
    host = base_url.split("//", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
    return host in _LOCAL_HOSTS


# ---------------------------------------------------------------------------
# document shaping
# ---------------------------------------------------------------------------


def _entries(document: Any) -> list:
    if not isinstance(document, dict) or not isinstance(document.get("models"), list):
        raise SettingsValidationError("models.yaml field 'models' must be a list")
    entries = document["models"]
    if not all(isinstance(entry, dict) for entry in entries):
        raise SettingsValidationError("every models.yaml entry must be a mapping")
    return entries


def _build_entry(fields: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    for key in MANAGED_KEYS:
        value = fields.get(key)
        if key in _OPTIONAL_KEYS and value is None:
            continue
        entry[key] = value
    return entry


def _update_entry(entry: dict, fields: dict[str, Any]) -> None:
    """Write the managed fields onto an existing entry, leaving the rest alone."""

    for key in MANAGED_KEYS:
        value = fields.get(key)
        if key in _OPTIONAL_KEYS and value is None:
            entry.pop(key, None)
        elif key == "price_per_1k_tokens" and isinstance(entry.get(key), dict):
            entry[key].update(value)  # in-place so comments inside the map survive
        else:
            entry[key] = value


def _form_entry(entry: dict) -> dict[str, Any]:
    prices = entry.get("price_per_1k_tokens")
    prices = prices if isinstance(prices, dict) else {}
    extra_body = entry.get("extra_body")
    return {
        "name": entry.get("name"),
        "type": entry.get("type"),
        "endpoint_url": entry.get("base_url"),
        "model_id": entry.get("model_id"),
        "pinned_revision": entry.get("pinned_revision"),
        "key_env": entry.get("api_key_env"),
        "concurrency": entry.get("concurrency", 1),
        "max_tokens": entry.get("max_tokens"),
        "extra_body": (
            "" if extra_body is None else yaml.safe_dump(extra_body, sort_keys=False)
        ),
        "price_input": prices.get("input"),
        "price_output": prices.get("output"),
        "inferencer": entry.get("inferencer"),
        "local": _is_local({"inferencer": entry.get("inferencer"), "base_url": entry.get("base_url")}),
        "other_keys": [key for key in entry if key not in MANAGED_KEYS],
    }
