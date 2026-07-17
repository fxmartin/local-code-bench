"""Comparison aggregation over raw benchmark results (story 17.1-001).

Turns ``results/*.jsonl`` into paired, comparable per-configuration statistics
so every number a comparison view shows is computed from the same re-scorable
JSONL as the leaderboard — never a hand-picked figure.

A *configuration* is one (model, engine, quant) triple observed in endpoint
records, statted per (suite, suite version, hardware tag) context so runs from
different suites, suite versions, or hardware are never silently averaged
together. Configurations of the same nominal model pair up via the Epic-11
:func:`inferencers.inventory.base_model_key` normalization; the gpt-oss
identical-weights pair is flagged as a controlled comparison.

Medians (and nearest-rank p95) over means throughout, for flaky-run tolerance.
Result files are read tolerantly like the dashboard loader: a truncated or
malformed line is skipped rather than discarding the whole run.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from math import ceil
from pathlib import Path
from statistics import median

from local_code_bench.engine_provenance import backend_label
from local_code_bench.inferencers.inventory import LocalModel, base_model_key, parse_quant

#: Base-model keys whose cross-engine pair is a controlled comparison: the same
#: published weights served by different engines, so any delta is the engine's.
_CONTROLLED_BASE_PREFIX = "gpt-oss"


@dataclass(frozen=True)
class MetricSummary:
    """Median and nearest-rank p95 of one per-task metric, with sample count."""

    median: float | None
    p95: float | None
    samples: int


@dataclass(frozen=True)
class ConfigurationStats:
    """Summary stats for one (model, engine, quant) configuration in one context.

    The context — suite, suite version, hardware tag — is part of the identity so
    stats from incomparable runs are never pooled. ``run_ids`` (result-file
    basenames) make every number traceable to its re-scorable raw JSONL.
    """

    model: str
    engine_label: str
    quant: str | None
    base_model_key: str
    suite: str | None
    suite_version: str | None
    hardware_tag: str | None
    run_ids: tuple[str, ...]
    attempts: int
    passed: int
    pass_at_1: float
    ttft: MetricSummary
    prefill_tokens_per_second: MetricSummary
    decode_tokens_per_second: MetricSummary
    latency: MetricSummary
    cost_per_task_usd: float
    memory_bytes: int | None


@dataclass(frozen=True)
class ExcludedConfiguration:
    """A configuration left out of a comparison, with the explicit reason why."""

    model: str
    engine_label: str
    quant: str | None
    base_model_key: str
    suite: str | None
    suite_version: str | None
    hardware_tag: str | None
    run_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Cohort:
    """Paired configurations of one base model sharing one comparable context."""

    base_model_key: str
    controlled: bool
    suite: str | None
    suite_version: str | None
    hardware_tag: str | None
    configurations: tuple[ConfigurationStats, ...]
    verdict_inputs: dict[str, dict[str, float | int | None]]


@dataclass(frozen=True)
class Axis:
    """One comparison dimension the dashboard can request by id."""

    id: str
    title: str
    description: str


@dataclass(frozen=True)
class AxisComparison:
    """Everything a comparison view needs for one axis: cohorts + exclusions."""

    axis: Axis
    cohorts: tuple[Cohort, ...]
    excluded: tuple[ExcludedConfiguration, ...]


_AXES: dict[str, Axis] = {
    "engine": Axis(
        id="engine",
        title="Same model across engines",
        description="Configurations of one base model served by different engines.",
    ),
    "quant": Axis(
        id="quant",
        title="Same model across quantizations",
        description="Configurations of one base model on one engine at different quants.",
    ),
    "gpt-oss": Axis(
        id="gpt-oss",
        title="gpt-oss identical-weights pair",
        description="Controlled comparison: identical published weights across engines.",
    ),
}


def is_controlled_pair(base_key: str) -> bool:
    """Whether a base model's cross-configuration pair is a controlled comparison."""

    return base_key.startswith(_CONTROLLED_BASE_PREFIX)


# ---------------------------------------------------------------------------
# configuration stats
# ---------------------------------------------------------------------------

#: (model, engine label, quant, suite, suite version, hardware tag)
_ConfigKey = tuple[str, str, str | None, str | None, str | None, str | None]

#: base-model-key -> quant (lower-cased, or None) -> size in bytes
MemoryIndex = Mapping[str, Mapping[str | None, int]]


def build_configuration_stats(
    result_paths: Sequence[str | Path],
    *,
    memory: MemoryIndex | None = None,
) -> tuple[ConfigurationStats, ...]:
    """Aggregate endpoint records into per-configuration, per-context stats.

    Records are deduped to the latest attempt per (configuration, task), matching
    leaderboard semantics. The hardware tag comes from each file's metadata
    header; the run id is the file's basename (secret-safe, like the dashboard's
    run history).
    """

    deduped: dict[tuple[_ConfigKey, str], tuple[dict[str, object], str]] = {}
    for path in result_paths:
        file_path = Path(path)
        run_id = file_path.name
        records = _read_records(file_path)
        hardware_tag = next(
            (
                str(record["hardware_tag"])
                for record in records
                if record.get("record_type") == "metadata"
                and isinstance(record.get("hardware_tag"), str)
            ),
            None,
        )
        for record in records:
            if record.get("record_type") in {"metadata", "power"}:
                continue
            if record.get("run_mode") != "endpoint":
                continue
            model = record.get("model")
            task_id = record.get("task_id")
            if not isinstance(model, str) or not isinstance(task_id, str):
                continue
            key: _ConfigKey = (
                model,
                backend_label(record.get("engine"), record.get("endpoint_provider")),
                parse_quant(model),
                _as_str(record.get("suite")),
                _as_str(record.get("suite_version")),
                hardware_tag,
            )
            deduped[(key, task_id)] = (record, run_id)

    grouped: dict[_ConfigKey, list[tuple[dict[str, object], str]]] = defaultdict(list)
    for (key, _task_id), entry in deduped.items():
        grouped[key].append(entry)

    stats = [
        _configuration_stats(key, entries, memory=memory)
        for key, entries in grouped.items()
    ]
    return tuple(
        sorted(
            stats,
            key=lambda config: (
                config.base_model_key,
                config.model,
                config.engine_label,
                config.suite or "",
                config.suite_version or "",
                config.hardware_tag or "",
            ),
        )
    )


def _configuration_stats(
    key: _ConfigKey,
    entries: list[tuple[dict[str, object], str]],
    *,
    memory: MemoryIndex | None,
) -> ConfigurationStats:
    model, engine, quant, suite, suite_version, hardware_tag = key
    records = [record for record, _run_id in entries]
    attempts = len(records)
    passed = sum(1 for record in records if record.get("passed") is True)
    costs = [_as_float(record.get("cost_usd")) or 0.0 for record in records]
    base_key = base_model_key(model)
    return ConfigurationStats(
        model=model,
        engine_label=engine,
        quant=quant,
        base_model_key=base_key,
        suite=suite,
        suite_version=suite_version,
        hardware_tag=hardware_tag,
        run_ids=tuple(sorted({run_id for _record, run_id in entries})),
        attempts=attempts,
        passed=passed,
        pass_at_1=passed / attempts if attempts else 0.0,
        ttft=_summary(records, "ttft_seconds"),
        prefill_tokens_per_second=_summary(records, "prefill_tokens_per_second"),
        decode_tokens_per_second=_summary(records, "decode_tokens_per_second"),
        latency=_summary(records, "latency_seconds"),
        cost_per_task_usd=sum(costs) / attempts if attempts else 0.0,
        memory_bytes=memory_for(memory, base_key, quant) if memory is not None else None,
    )


def _summary(records: Iterable[dict[str, object]], metric: str) -> MetricSummary:
    values = sorted(
        value
        for record in records
        for value in (_metric(record, metric),)
        if value is not None
    )
    if not values:
        return MetricSummary(median=None, p95=None, samples=0)
    # Nearest-rank p95: stable for the small sample counts a suite run produces.
    p95_index = max(0, ceil(0.95 * len(values)) - 1)
    return MetricSummary(
        median=float(median(values)),
        p95=values[p95_index],
        samples=len(values),
    )


# ---------------------------------------------------------------------------
# memory footprint (from inventory, where known)
# ---------------------------------------------------------------------------


def memory_index(models: Iterable[LocalModel]) -> dict[str, dict[str | None, int]]:
    """Index inventory sizes by base-model key and quant for footprint lookup."""

    index: dict[str, dict[str | None, int]] = defaultdict(dict)
    for model in models:
        quant = model.quant or parse_quant(model.name)
        quant_key = quant.lower() if quant is not None else None
        by_quant = index[base_model_key(model.name)]
        by_quant[quant_key] = max(by_quant.get(quant_key, 0), model.size_bytes)
    return dict(index)


def memory_for(memory: MemoryIndex | None, base_key: str, quant: str | None) -> int | None:
    """Look up a configuration's on-disk footprint; ``None`` when unknown.

    Prefers an exact quant match; falls back to the sole inventory entry for the
    base model so a differently-labelled quant still resolves unambiguously.
    """

    if memory is None:
        return None
    by_quant = memory.get(base_key)
    if not by_quant:
        return None
    quant_key = quant.lower() if quant is not None else None
    if quant_key in by_quant:
        return by_quant[quant_key]
    if len(by_quant) == 1:
        return next(iter(by_quant.values()))
    return None


# ---------------------------------------------------------------------------
# axis comparison
# ---------------------------------------------------------------------------

_Context = tuple[str | None, str | None, str | None]  # (suite, suite_version, hardware_tag)


def compare_axis(
    stats: Sequence[ConfigurationStats], axis_id: str
) -> AxisComparison | None:
    """Pair configurations along one axis; ``None`` for an unknown axis.

    Within each pairing group (base model, plus engine for the quant axis),
    configurations pair only inside a shared (suite, suite version, hardware tag)
    context. Configurations whose context has no comparable partner are excluded
    with an explicit reason naming the mismatching field — never averaged in.
    """

    axis = _AXES.get(axis_id)
    if axis is None:
        return None
    rows = [
        config
        for config in stats
        if axis_id != "gpt-oss" or is_controlled_pair(config.base_model_key)
    ]

    groups: dict[tuple[str, ...], list[ConfigurationStats]] = defaultdict(list)
    for config in rows:
        groups[_group_key(axis_id, config)].append(config)

    cohorts: list[Cohort] = []
    excluded: list[ExcludedConfiguration] = []
    for _key, group_rows in sorted(groups.items()):
        contexts: dict[_Context, list[ConfigurationStats]] = defaultdict(list)
        for config in group_rows:
            contexts[(config.suite, config.suite_version, config.hardware_tag)].append(config)
        paired = {
            context: members
            for context, members in contexts.items()
            if len({_axis_value(axis_id, config) for config in members}) >= 2
        }
        if paired:
            reference = min(
                paired,
                key=lambda context: (-len(paired[context]), _context_sort(context)),
            )
        elif len(contexts) >= 2:
            # Configurations exist but no context holds a pair: everything outside
            # the reference context is incomparable and must be excluded loudly.
            reference = min(contexts, key=_context_sort)
        else:
            continue  # a lone configuration has nothing to pair with

        for context in sorted(paired, key=_context_sort):
            members = tuple(
                sorted(
                    paired[context],
                    key=lambda config: (config.engine_label, config.quant or "", config.model),
                )
            )
            suite, suite_version, hardware_tag = context
            cohorts.append(
                Cohort(
                    base_model_key=members[0].base_model_key,
                    controlled=is_controlled_pair(members[0].base_model_key),
                    suite=suite,
                    suite_version=suite_version,
                    hardware_tag=hardware_tag,
                    configurations=members,
                    verdict_inputs=_verdict_inputs(members),
                )
            )
        for context in sorted(contexts, key=_context_sort):
            if context in paired or context == reference:
                continue
            reason = _mismatch_reason(context, reference)
            excluded.extend(
                ExcludedConfiguration(
                    model=config.model,
                    engine_label=config.engine_label,
                    quant=config.quant,
                    base_model_key=config.base_model_key,
                    suite=config.suite,
                    suite_version=config.suite_version,
                    hardware_tag=config.hardware_tag,
                    run_ids=config.run_ids,
                    reason=reason,
                )
                for config in sorted(contexts[context], key=lambda c: (c.model, c.engine_label))
            )
    return AxisComparison(axis=axis, cohorts=tuple(cohorts), excluded=tuple(excluded))


def compare_action(
    result_paths: Sequence[str | Path],
    axis_id: str,
    *,
    memory: MemoryIndex | None = None,
) -> tuple[int, dict[str, object]]:
    """Build the ``GET /api/compare?axis=<id>`` payload: ``(status, JSON body)``.

    Unknown (or missing) axis is a 404 listing the available axis ids.
    """

    comparison = compare_axis(build_configuration_stats(result_paths, memory=memory), axis_id)
    if comparison is None:
        return 404, {"error": f"unknown axis: {axis_id!r}", "axes": sorted(_AXES)}
    # Round-trip through JSON so tuples become lists and the payload is provably
    # serializable exactly as the browser will receive it.
    return 200, json.loads(json.dumps(asdict(comparison)))


def _group_key(axis_id: str, config: ConfigurationStats) -> tuple[str, ...]:
    if axis_id == "quant":
        return (config.base_model_key, config.engine_label)
    return (config.base_model_key,)


def _axis_value(axis_id: str, config: ConfigurationStats) -> str:
    if axis_id == "engine":
        return config.engine_label
    if axis_id == "quant":
        return config.quant or "unquantized"
    return f"{config.model} [{config.engine_label}]"


def _verdict_inputs(
    members: Sequence[ConfigurationStats],
) -> dict[str, dict[str, float | int | None]]:
    def key(config: ConfigurationStats) -> str:
        return f"{config.model} [{config.engine_label}]"

    return {
        "pass_at_1": {key(c): c.pass_at_1 for c in members},
        "median_ttft_seconds": {key(c): c.ttft.median for c in members},
        "p95_ttft_seconds": {key(c): c.ttft.p95 for c in members},
        "median_prefill_tokens_per_second": {
            key(c): c.prefill_tokens_per_second.median for c in members
        },
        "median_decode_tokens_per_second": {
            key(c): c.decode_tokens_per_second.median for c in members
        },
        "median_latency_seconds": {key(c): c.latency.median for c in members},
        "p95_latency_seconds": {key(c): c.latency.p95 for c in members},
        "cost_per_task_usd": {key(c): c.cost_per_task_usd for c in members},
        "memory_bytes": {key(c): c.memory_bytes for c in members},
    }


def _mismatch_reason(context: _Context, reference: _Context) -> str:
    names = ("suite", "suite_version", "hardware_tag")
    diffs = [
        f"{name} mismatch: {value!r} != {expected!r}"
        for name, value, expected in zip(names, context, reference, strict=True)
        if value != expected
    ]
    return "; ".join(diffs) or "no comparable configuration shares this context"


def _context_sort(context: _Context) -> tuple[str, str, str]:
    suite, suite_version, hardware_tag = context
    return (suite or "", suite_version or "", hardware_tag or "")


def _read_records(path: Path) -> list[dict[str, object]]:
    """Read one result file tolerantly: skip unreadable or non-object lines."""

    records: list[dict[str, object]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def _metric(record: dict[str, object], key: str) -> float | None:
    metrics = record.get("metrics")
    if isinstance(metrics, dict):
        return _as_float(metrics.get(key))
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
