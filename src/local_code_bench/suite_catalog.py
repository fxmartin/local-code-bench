"""Available-suites catalog for the benchmark launcher.

Enumerates the built-in benchmark suites plus any custom suites registered in an
optional ``configs/suites.yaml`` so the dashboard launcher (Story 09.2-001) can
offer every suite — built-in or custom — without a code change. Availability and
task counts reuse the Epic-02 loaders/dataset cache (``tasks.py``) so a suite that
cannot be loaded now (e.g. a missing EvalPlus cache file) is shown disabled with a
reason instead of being offered and failing at launch.

The payload is JSON-serializable and carries no secrets or host paths, so it can be
surfaced directly as a localhost-only dashboard endpoint.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from .config import ConfigError
from .settings import get_settings
from .tasks import (
    CANARY_HUMANEVAL_IDS,
    EVALPLUS_FILENAMES,
    TaskLoadError,
    _find_cached,
    load_evalplus,
    load_mbpp,
)

SuiteKind = Literal["builtin", "custom"]

DEFAULT_CACHE_DIR = get_settings().cache_dir
DEFAULT_SUITES_PATH = "configs/suites.yaml"

# Built-in suites in the order the launcher should present them. ``downloadable``
# suites are fetched on first use (HumanEval/MBPP and the canary anchor subset that
# derives from HumanEval), so they are always offered; the EvalPlus variants require
# a manually-cached release file and are only offered when that file is present.
_HUMANEVAL_TASK_COUNT = 164  # the loader pins HumanEval to exactly 164 tasks
_CANARY_TASK_COUNT = len(CANARY_HUMANEVAL_IDS)
_MBPP_CACHE_FILENAME = "sanitized-mbpp.json"

BUILTIN_SUITE_IDS: tuple[str, ...] = (
    "humaneval",
    "mbpp",
    "canary",
    "humaneval-plus",
    "mbpp-plus",
)

_BUILTIN_LABELS: dict[str, str] = {
    "humaneval": "HumanEval",
    "mbpp": "MBPP",
    "canary": "Canary (HumanEval anchor)",
    "humaneval-plus": "HumanEval+",
    "mbpp-plus": "MBPP+",
}


@dataclass(frozen=True)
class SuiteCatalogEntry:
    """One selectable suite as the launcher should see it.

    ``available`` gates whether the suite can be launched now; when it is False,
    ``reason`` explains why (e.g. a missing cache file) so the UI can disable it
    with an explanation rather than failing at launch. ``task_count`` is the number
    of tasks when known without a network round-trip, else None.
    """

    id: str
    label: str
    kind: SuiteKind
    available: bool
    task_count: int | None
    reason: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CustomSuiteDef:
    """A config-registered custom suite: an id pointing at a loadable dataset."""

    id: str
    source: str
    label: str | None = None
    format: str | None = None


def suite_catalog(
    *,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    suites_path: str | Path = DEFAULT_SUITES_PATH,
) -> list[SuiteCatalogEntry]:
    """Enumerate built-in and custom suites with availability and counts."""

    cache = Path(cache_dir)
    entries = [_builtin_entry(suite_id, cache) for suite_id in BUILTIN_SUITE_IDS]
    entries.extend(_custom_entries(suites_path))
    return entries


def catalog_payload(
    *,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    suites_path: str | Path = DEFAULT_SUITES_PATH,
) -> dict[str, Any]:
    """JSON-serializable catalog payload for the launcher endpoint (Story 09.2-001)."""

    catalog = suite_catalog(cache_dir=cache_dir, suites_path=suites_path)
    return {"suites": [entry.to_dict() for entry in catalog]}


def _builtin_entry(suite_id: str, cache: Path) -> SuiteCatalogEntry:
    label = _BUILTIN_LABELS[suite_id]
    if suite_id == "humaneval":
        return SuiteCatalogEntry(suite_id, label, "builtin", True, _HUMANEVAL_TASK_COUNT)
    if suite_id == "canary":
        return SuiteCatalogEntry(suite_id, label, "builtin", True, _CANARY_TASK_COUNT)
    if suite_id == "mbpp":
        return SuiteCatalogEntry(suite_id, label, "builtin", True, _mbpp_count(cache))
    # EvalPlus variants: only offered when their release file is cached.
    return _evalplus_entry(suite_id, label, cache)


def _mbpp_count(cache: Path) -> int | None:
    """Count MBPP tasks only when cached; never trigger a download for the catalog."""

    if not (cache / _MBPP_CACHE_FILENAME).exists():
        return None
    try:
        return len(load_mbpp(cache_dir=cache))
    except TaskLoadError:
        return None


def _evalplus_entry(suite_id: str, label: str, cache: Path) -> SuiteCatalogEntry:
    if _find_cached(cache, EVALPLUS_FILENAMES[suite_id]) is None:
        wanted = " or ".join(EVALPLUS_FILENAMES[suite_id])
        reason = f"requires a cached EvalPlus release file ({wanted}) in {cache.name}/"
        return SuiteCatalogEntry(suite_id, label, "builtin", False, None, reason=reason)
    try:
        count = len(load_evalplus(suite_id, cache_dir=cache))
    except TaskLoadError as exc:
        return SuiteCatalogEntry(suite_id, label, "builtin", False, None, reason=str(exc))
    return SuiteCatalogEntry(suite_id, label, "builtin", True, count)


def _custom_entries(suites_path: str | Path) -> list[SuiteCatalogEntry]:
    defs = load_custom_suites(suites_path)
    base = Path(suites_path).resolve().parent
    return [_custom_entry(definition, base) for definition in defs]


def _custom_entry(definition: CustomSuiteDef, base: Path) -> SuiteCatalogEntry:
    label = definition.label or definition.id
    source = (base / definition.source).resolve()
    if not source.exists():
        return SuiteCatalogEntry(
            definition.id,
            label,
            "custom",
            False,
            None,
            reason=f"source file not found: {definition.source}",
            source=definition.source,
        )
    try:
        count = _count_dataset(source, definition.format)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return SuiteCatalogEntry(
            definition.id,
            label,
            "custom",
            False,
            None,
            reason=f"could not read dataset: {exc}",
            source=definition.source,
        )
    return SuiteCatalogEntry(
        definition.id,
        label,
        "custom",
        True,
        count,
        source=definition.source,
    )


def _count_dataset(path: Path, fmt: str | None) -> int:
    """Count records in a custom dataset (jsonl or a json list/`data` list)."""

    resolved = (fmt or _infer_format(path)).lower()
    if resolved == "jsonl":
        return _count_jsonl(path)
    if resolved == "json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(rows, list):
            raise ValueError("json dataset must be a list or contain a 'data' list")
        return len(rows)
    raise ValueError(f"unsupported dataset format '{resolved}'")


def _infer_format(path: Path) -> str:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if ".jsonl" in suffixes:
        return "jsonl"
    if ".json" in suffixes:
        return "json"
    raise ValueError(f"cannot infer dataset format from '{path.name}'")


def _count_jsonl(path: Path) -> int:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as file:
        return sum(1 for line in file if line.strip())


def load_custom_suites(path: str | Path) -> list[CustomSuiteDef]:
    """Load custom-suite definitions from an optional ``suites.yaml``.

    A missing file is not an error — it simply means no custom suites are
    registered. Ids must be unique and must not collide with a built-in suite.
    """

    config_path = Path(path)
    if not config_path.exists():
        return []
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if raw is None:
        return []
    if not isinstance(raw, dict) or not isinstance(raw.get("suites"), list):
        raise ConfigError("suites.yaml field 'suites' must be a list")

    suites: list[CustomSuiteDef] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw["suites"]):
        definition = _parse_custom_suite(entry, index)
        if definition.id in seen:
            raise ConfigError(f"suites[{index}].id duplicates '{definition.id}'")
        if definition.id in BUILTIN_SUITE_IDS:
            raise ConfigError(
                f"suites[{index}].id '{definition.id}' collides with a built-in suite"
            )
        seen.add(definition.id)
        suites.append(definition)
    return suites


def _parse_custom_suite(entry: Any, index: int) -> CustomSuiteDef:
    if not isinstance(entry, dict):
        raise ConfigError(f"suites[{index}] must be a mapping")
    suite_id = _required_str(entry, "id", index)
    source = _required_str(entry, "source", index)
    label = entry.get("label")
    if label is not None and not isinstance(label, str):
        raise ConfigError(f"suites[{index}].label must be a string")
    fmt = entry.get("format")
    if fmt is not None and not isinstance(fmt, str):
        raise ConfigError(f"suites[{index}].format must be a string")
    return CustomSuiteDef(id=suite_id, source=source, label=label, format=fmt)


def _required_str(entry: dict[str, Any], field: str, index: int) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"suites[{index}].{field} must be a non-empty string")
    return value


def builtin_suite_ids() -> Iterable[str]:
    """The built-in suite ids, in launcher presentation order."""

    return BUILTIN_SUITE_IDS
