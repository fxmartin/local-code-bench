"""Report payloads for the Benchmarks tab's comparison view (story 17.2-001).

The tab renders one declared comparison axis (``configs/comparisons.yaml``,
story 17.1-002) as a designed report — hero, methodology chips, paired stat
panels, Pareto frontier — and this module builds everything it shows from the
17.1-001 per-configuration stats plus each run's metadata header. Two payloads:

* :func:`axes_action` — the axis picker: every catalog axis with a
  ``data_ready`` flag (data-ready axes first) and, per cohort, which configured
  models already have results vs which still need a run.
* :func:`report_action` — one axis as report data: hero sides, a subtitle
  stating the controlled variables, methodology chips (engine versions, suite,
  seed/temp, hardware, run dates), per-member paired stats with side
  assignment and controlled-pair badges, the cross-cutting Pareto frontier
  (pass@1 vs decode tok/s, memory-sized points), and context-scaling series
  where sweep data exists.

Everything here is pure over its inputs and JSON-safe, so the browser client
stays thin and a future PDF export can reuse the same payloads.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from local_code_bench.compare import ConfigurationStats, _read_records
from local_code_bench.compare_verdicts import CanaryHistory, conclusions
from local_code_bench.config import (
    ComparisonAxis,
    ComparisonCatalog,
    ConfigError,
    ModelConfig,
    cohort_model_names,
    load_comparisons,
)
from local_code_bench.dashboard_model import SweepPoint


def load_catalog_safe(path: str | Path) -> ComparisonCatalog:
    """Load the comparison catalog, degrading a file-level failure to an error.

    A missing or unparsable file must not take the Benchmarks tab down — the
    picker then shows no axes plus the loader error, mirroring how the other
    dashboard config surfaces degrade.
    """

    try:
        return load_comparisons(path)
    except ConfigError as exc:
        return ComparisonCatalog(axes=(), errors=(str(exc),))


def read_run_metadata(result_paths: Sequence[str | Path]) -> dict[str, dict[str, object]]:
    """Each run's metadata header, keyed by the run id (file basename).

    The basename is the same run id :class:`ConfigurationStats` carries, so the
    report can attach methodology (engine versions, seed/temp, dates) to exactly
    the runs that back its numbers. Files without a metadata record are skipped.
    """

    metadata: dict[str, dict[str, object]] = {}
    for path in result_paths:
        file_path = Path(path)
        for record in _read_records(file_path):
            if record.get("record_type") == "metadata":
                metadata[file_path.name] = record
                break
    return metadata


def pareto_frontier(points: Sequence[tuple[float, float]]) -> set[int]:
    """Indices of the Pareto-optimal points (both coordinates maximized).

    A point is on the frontier when no other point is at least as good on both
    coordinates and strictly better on one. O(n²) is fine for the handful of
    configurations a benchmark box accumulates.
    """

    frontier: set[int] = set()
    for i, (xi, yi) in enumerate(points):
        dominated = any(
            xj >= xi and yj >= yi and (xj > xi or yj > yi)
            for j, (xj, yj) in enumerate(points)
            if j != i
        )
        if not dominated:
            frontier.add(i)
    return frontier


# ---------------------------------------------------------------------------
# axis picker
# ---------------------------------------------------------------------------


def axes_action(
    catalog: ComparisonCatalog,
    stats: Sequence[ConfigurationStats],
    models: Mapping[str, ModelConfig],
) -> tuple[int, dict[str, object]]:
    """Build the ``GET /api/compare/axes`` payload: the picker's axis list.

    Axes with data lead (stable within each group, preserving catalog order);
    an axis is data-ready when at least two of its cohorts match a
    configuration that already has results.
    """

    axes = [
        {
            "id": axis.id,
            "title": axis.title,
            "description": axis.description,
            "data_ready": _is_data_ready(axis, stats, models),
            "cohorts": _cohort_summaries(axis, stats, models),
        }
        for axis in catalog.axes
    ]
    axes.sort(key=lambda axis: not axis["data_ready"])
    return 200, {"axes": axes, "errors": list(catalog.errors)}


# ---------------------------------------------------------------------------
# per-axis report
# ---------------------------------------------------------------------------


def report_action(
    catalog: ComparisonCatalog,
    stats: Sequence[ConfigurationStats],
    axis_id: str,
    *,
    models: Mapping[str, ModelConfig],
    sweep_points: Sequence[SweepPoint] = (),
    metadata_by_run: Mapping[str, dict[str, object]] | None = None,
    canary_history: CanaryHistory | None = None,
) -> tuple[int, dict[str, object]]:
    """Build the ``GET /api/compare/report?axis=<id>`` payload for one axis.

    Unknown (or missing) axis is a 404 listing the declared axis ids.
    ``canary_history`` feeds the axis's ``canary_drift`` verdict rules (story
    17.2-002); axes without one never read it.
    """

    axis = catalog.axis(axis_id)
    if axis is None:
        return 404, {
            "error": f"unknown axis: {axis_id!r}",
            "axes": [declared.id for declared in catalog.axes],
        }

    assigned = sorted(
        (
            (side, stat)
            for stat in stats
            for side in (_side_for_model(axis, stat.model, models, quant=stat.quant),)
            if side is not None
        ),
        key=lambda pair: (pair[0], pair[1].model, pair[1].engine_label, pair[1].suite or ""),
    )
    badges = _controlled_badges(axis, {stat.model for _side, stat in assigned})
    members = [_member(axis, side, stat, badges) for side, stat in assigned]
    metadata = _contributing_metadata(assigned, metadata_by_run or {})

    return 200, {
        "axis": {"id": axis.id, "title": axis.title, "description": axis.description},
        "data_ready": _is_data_ready(axis, stats, models),
        "sides": _cohort_summaries(axis, stats, models),
        "subtitle": _subtitle(assigned, metadata),
        "chips": _chips(assigned, metadata),
        "conclusions": conclusions(
            axis, assigned, models=models, canary_history=canary_history
        ),
        "members": members,
        "frontier": _frontier(axis, stats, models),
        "context_scaling": _context_scaling(axis, sweep_points, models),
    }


# ---------------------------------------------------------------------------
# cohort membership
# ---------------------------------------------------------------------------


def _side_for_model(
    axis: ComparisonAxis,
    model_name: str,
    models: Mapping[str, ModelConfig],
    *,
    quant: str | None = None,
) -> int | None:
    """The cohort index a model belongs to on this axis, or ``None``.

    Cohort filters match on the *declared* registry fields (inferencer, quant)
    exactly like :func:`config.cohort_model_names`; the configuration's parsed
    quant is the fallback when the registry does not declare one.
    """

    config = models.get(model_name)
    inferencer = config.inferencer if config is not None else None
    declared_quant = config.quant if config is not None and config.quant else quant
    for index, cohort in enumerate(axis.cohorts):
        if cohort.matches(model_name, inferencer=inferencer, quant=declared_quant):
            return index
    return None


def _matched_models(
    cohort_index: int,
    axis: ComparisonAxis,
    stats: Sequence[ConfigurationStats],
    models: Mapping[str, ModelConfig],
) -> list[str]:
    return sorted(
        {
            stat.model
            for stat in stats
            if _side_for_model(axis, stat.model, models, quant=stat.quant) == cohort_index
        }
    )


def _cohort_summaries(
    axis: ComparisonAxis,
    stats: Sequence[ConfigurationStats],
    models: Mapping[str, ModelConfig],
) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for index, cohort in enumerate(axis.cohorts):
        matched = _matched_models(index, axis, stats, models)
        summaries.append(
            {
                "name": cohort.name,
                "index": index,
                "matched": matched,
                "models_to_run": [
                    name for name in cohort_model_names(cohort, models) if name not in matched
                ],
            }
        )
    return summaries


def _is_data_ready(
    axis: ComparisonAxis,
    stats: Sequence[ConfigurationStats],
    models: Mapping[str, ModelConfig],
) -> bool:
    populated = sum(
        1
        for index in range(len(axis.cohorts))
        if _matched_models(index, axis, stats, models)
    )
    return populated >= 2


def _controlled_badges(
    axis: ComparisonAxis, present_models: set[str]
) -> dict[str, dict[str, str]]:
    """Model -> controlled badge, for highlighted pairs with ≥2 members present.

    A lone half of a controlled pair is not badged: the badge asserts the clean
    A/B is actually on screen, not merely declared.
    """

    badges: dict[str, dict[str, str]] = {}
    for pair in axis.highlighted_pairs:
        present = [name for name in pair.models if name in present_models]
        if len(present) < 2:
            continue
        for name in present:
            badges.setdefault(name, {"reason": pair.reason})
    return badges


def _member(
    axis: ComparisonAxis,
    side: int,
    stat: ConfigurationStats,
    badges: Mapping[str, dict[str, str]],
) -> dict[str, object]:
    return {
        "model": stat.model,
        "engine_label": stat.engine_label,
        "quant": stat.quant,
        "side": side,
        "side_name": axis.cohorts[side].name,
        "controlled": badges.get(stat.model),
        "suite": stat.suite,
        "suite_version": stat.suite_version,
        "hardware_tag": stat.hardware_tag,
        "run_ids": list(stat.run_ids),
        "stats": {
            "pass_at_1": stat.pass_at_1,
            "prefill_tokens_per_second": stat.prefill_tokens_per_second.median,
            "decode_tokens_per_second": stat.decode_tokens_per_second.median,
            "ttft_seconds": stat.ttft.median,
            "cost_per_task_usd": stat.cost_per_task_usd,
            "attempts": stat.attempts,
        },
    }


# ---------------------------------------------------------------------------
# methodology: subtitle + chips from the contributing runs' metadata
# ---------------------------------------------------------------------------


def _contributing_metadata(
    assigned: Sequence[tuple[int, ConfigurationStats]],
    metadata_by_run: Mapping[str, dict[str, object]],
) -> list[dict[str, object]]:
    run_ids = sorted({run_id for _side, stat in assigned for run_id in stat.run_ids})
    return [metadata_by_run[run_id] for run_id in run_ids if run_id in metadata_by_run]


def _suite_labels(assigned: Sequence[tuple[int, ConfigurationStats]]) -> list[str]:
    labels = {
        f"{stat.suite} v{stat.suite_version}" if stat.suite_version else stat.suite
        for _side, stat in assigned
        if stat.suite
    }
    return sorted(labels)


def _seed_temp_labels(metadata: Sequence[dict[str, object]]) -> list[str]:
    combos = {
        (record["seed"], record["temperature"])
        for record in metadata
        if isinstance(record.get("seed"), int) and isinstance(record.get("temperature"), (int, float))
    }
    return [f"seed {seed} · temp {temperature}" for seed, temperature in sorted(combos)]


def _hardware_labels(assigned: Sequence[tuple[int, ConfigurationStats]]) -> list[str]:
    return sorted({stat.hardware_tag for _side, stat in assigned if stat.hardware_tag})


def _run_dates_label(metadata: Sequence[dict[str, object]]) -> str | None:
    dates = sorted(
        {
            str(record["timestamp"]).split("T", 1)[0]
            for record in metadata
            if isinstance(record.get("timestamp"), str)
        }
    )
    if not dates:
        return None
    if dates[0] == dates[-1]:
        return dates[0]
    return f"{dates[0]} – {dates[-1]}"


def _engine_version_labels(
    assigned: Sequence[tuple[int, ConfigurationStats]],
    metadata: Sequence[dict[str, object]],
) -> list[str]:
    member_models = {stat.model for _side, stat in assigned}
    labels: set[str] = set()
    for record in metadata:
        declared = record.get("models")
        if not isinstance(declared, dict):
            continue
        for name, details in declared.items():
            if name not in member_models or not isinstance(details, dict):
                continue
            engine = details.get("engine")
            versions = engine.get("versions") if isinstance(engine, dict) else None
            if not isinstance(versions, dict):
                continue
            labels.update(
                f"{component} {version}" for component, version in versions.items()
            )
    return sorted(labels)


def _subtitle(
    assigned: Sequence[tuple[int, ConfigurationStats]],
    metadata: Sequence[dict[str, object]],
) -> str:
    if not assigned:
        return "No comparable runs yet — run the listed models to populate this comparison."
    parts = [f"suite {label}" for label in _suite_labels(assigned)]
    parts.extend(f"hardware {label}" for label in _hardware_labels(assigned))
    parts.extend(_seed_temp_labels(metadata))
    if not parts:
        return "Controlled variables unavailable for these runs."
    return "Controlled: " + " · ".join(parts)


def _chips(
    assigned: Sequence[tuple[int, ConfigurationStats]],
    metadata: Sequence[dict[str, object]],
) -> list[dict[str, str]]:
    chips = [
        {"label": "engine", "value": label}
        for label in _engine_version_labels(assigned, metadata)
    ]
    chips.extend({"label": "suite", "value": label} for label in _suite_labels(assigned))
    chips.extend({"label": "seed / temp", "value": label} for label in _seed_temp_labels(metadata))
    chips.extend({"label": "hardware", "value": label} for label in _hardware_labels(assigned))
    dates = _run_dates_label(metadata)
    if dates is not None:
        chips.append({"label": "run dates", "value": dates})
    return chips


# ---------------------------------------------------------------------------
# cross-cutting sections: Pareto frontier + context scaling
# ---------------------------------------------------------------------------


def _frontier(
    axis: ComparisonAxis,
    stats: Sequence[ConfigurationStats],
    models: Mapping[str, ModelConfig],
) -> list[dict[str, object]]:
    """Pass@1 vs decode tok/s for *every* configuration with data.

    The frontier is cross-cutting by design: whatever axis is selected, the
    chart shows where its cohorts sit against the whole field. Points inside
    the axis's cohorts carry their side index so the client can color them.
    """

    plottable: list[tuple[ConfigurationStats, float]] = []
    for stat in stats:
        decode_median = stat.decode_tokens_per_second.median
        if decode_median is not None:
            plottable.append((stat, decode_median))
    optimal = pareto_frontier(
        [(decode_median, stat.pass_at_1) for stat, decode_median in plottable]
    )
    return [
        {
            "model": stat.model,
            "engine_label": stat.engine_label,
            "label": f"{stat.model} [{stat.engine_label}]",
            "decode_tokens_per_second": decode_median,
            "pass_at_1": stat.pass_at_1,
            "memory_bytes": stat.memory_bytes,
            "side": _side_for_model(axis, stat.model, models, quant=stat.quant),
            "frontier": index in optimal,
        }
        for index, (stat, decode_median) in enumerate(plottable)
    ]


def _context_scaling(
    axis: ComparisonAxis,
    sweep_points: Sequence[SweepPoint],
    models: Mapping[str, ModelConfig],
) -> list[dict[str, object]]:
    """Per model/engine (context tokens → prefill, TTFT) series from sweep runs."""

    grouped: dict[tuple[str, str], list[SweepPoint]] = defaultdict(list)
    for point in sweep_points:
        grouped[(point.model, point.engine_label)].append(point)

    series = [
        {
            "model": model,
            "engine_label": engine_label,
            "label": f"{model} [{engine_label}]",
            "side": _side_for_model(axis, model, models),
            "points": [
                {
                    "context_tokens": point.context_tokens,
                    "ttft_seconds": point.ttft_seconds,
                    "prefill_tokens_per_second": point.prefill_tokens_per_second,
                }
                for point in sorted(points, key=lambda point: point.context_tokens)
            ],
        }
        for (model, engine_label), points in grouped.items()
    ]
    series.sort(key=lambda entry: (entry["side"] is None, entry["side"] or 0, entry["label"]))
    return series
