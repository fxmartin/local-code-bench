"""Deterministic conclusion callouts for the comparison report (story 17.2-002).

Each axis's verdict rules — declared in ``configs/comparisons.yaml`` as rule id
plus params (:class:`config.VerdictRule`) — are evaluated here as pure
functions over the 17.1-001 per-configuration aggregates, and rendered as
templated prose with the computed numbers inline. Prose lives in templates
keyed by rule id (with per-kind defaults), so tuning a conclusion's wording
never touches the evaluation.

This is the layer that must never overreach — silence over spin:

* every callout lists its supporting run IDs and the threshold it applied;
* one-sided or missing data yields a callout stating what is missing, never a
  conclusion from partial data;
* a value within a rule's declared noise margin of its threshold is always
  phrased "inconclusive — within noise margin";
* configurations are only ever compared inside a shared (suite, suite
  version, hardware tag) context, mirroring the 17.1-001 pairing rules.

Three rule kinds: ``pair`` thresholds one cohort's aggregate against
another's, ``quality_bar`` finds the smallest configuration within the
threshold of the best, and ``canary_drift`` thresholds the latest canary run
against the previous one (:func:`canary_history` extracts the per-run series
from the raw result files).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from local_code_bench.compare import ConfigurationStats, _read_records
from local_code_bench.config import (
    ComparisonAxis,
    ModelConfig,
    VerdictRule,
    cohort_model_names,
)
from local_code_bench.engine_provenance import backend_label

#: metric name -> (prose label, higher is better, value formatter)
_METRICS: dict[str, tuple[str, bool, Callable[[float], str]]] = {
    "pass_at_1": ("pass@1", True, lambda v: f"{v * 100:.1f}%"),
    "median_ttft_seconds": ("median TTFT", False, lambda v: f"{v:.2f}s"),
    "p95_ttft_seconds": ("p95 TTFT", False, lambda v: f"{v:.2f}s"),
    "median_prefill_tokens_per_second": (
        "median prefill tok/s",
        True,
        lambda v: f"{v:.1f} tok/s",
    ),
    "median_decode_tokens_per_second": (
        "median decode tok/s",
        True,
        lambda v: f"{v:.1f} tok/s",
    ),
    "median_latency_seconds": ("median latency", False, lambda v: f"{v:.2f}s"),
    "p95_latency_seconds": ("p95 latency", False, lambda v: f"{v:.2f}s"),
    "cost_per_task_usd": ("cost per task", False, lambda v: f"${v:.4f}"),
    "memory_bytes": ("memory footprint", False, lambda v: f"{v / 1e9:.1f} GB"),
}

#: unit -> formatter for computed comparison values, thresholds, and margins.
_UNIT_FORMATS: dict[str | None, Callable[[float], str]] = {
    "ratio": lambda v: f"{v:.2f}×",
    "pp": lambda v: f"{v:.1f}pp",
    "usd": lambda v: f"${v:.3f}",
    None: lambda v: f"{v:g}",
}

_INCONCLUSIVE = "inconclusive — within noise margin"

#: Prose templates keyed by rule id; anything not listed falls back to the
#: per-kind defaults below. Placeholders are the keys of the fields dict each
#: evaluator builds.
_TEMPLATES: dict[str, dict[str, str]] = {
    "moe-prefill-gain": {
        "holds": (
            "MoE moved prefill {value} over dense{where}: {a_config} at {a_value} vs "
            "{b_config} at {b_value} (threshold {threshold}) — prefill is no longer the bound."
        ),
        "fails": (
            "MoE moved prefill only {value} over dense{where}: {a_config} at {a_value} vs "
            "{b_config} at {b_value} (threshold {threshold}): still prefill-bound."
        ),
    },
    "moe-decode-gain": {
        "holds": (
            "MoE moved decode {value} over dense{where}: {a_config} at {a_value} vs "
            "{b_config} at {b_value} (threshold {threshold})."
        ),
        "fails": (
            "MoE moved decode only {value} over dense{where}: {a_config} at {a_value} vs "
            "{b_config} at {b_value} (threshold {threshold})."
        ),
    },
}

_DEFAULT_PAIR = {
    "holds": (
        "{a_name} ({a_config}) vs {b_name} ({b_config}) on {metric}{where}: "
        "{a_value} vs {b_value} — {value}, at or above the {threshold} threshold."
    ),
    "fails": (
        "{a_name} ({a_config}) vs {b_name} ({b_config}) on {metric}{where}: "
        "{a_value} vs {b_value} — {value}, below the {threshold} threshold."
    ),
    "inconclusive": (
        _INCONCLUSIVE + ": {a_name} ({a_config}) vs {b_name} ({b_config}) on "
        "{metric}{where} is {value} against the {threshold} threshold (±{margin})."
    ),
}

_DEFAULT_QUALITY_BAR = {
    "holds": (
        "Smallest configuration clearing the quality bar{where}: {config} ({side_name}) "
        "at {value} pass@1, {gap} behind the best ({best_config} at {best_value}); "
        "bar: within {threshold} of best."
    ),
    "inconclusive": (
        _INCONCLUSIVE + ": the smallest configuration within the {threshold} "
        "quality bar{where} flips inside ±{margin}."
    ),
}

_DEFAULT_DRIFT = {
    "holds": (
        "Canary drift on {config}: pass@1 {latest_value} ({latest_date}) vs "
        "{prev_value} ({prev_date}) — {drift}, beyond the ±{threshold} tolerance."
    ),
    "fails": (
        "Canary steady on {config}: pass@1 {latest_value} ({latest_date}) vs "
        "{prev_value} ({prev_date}) — {drift}, within the ±{threshold} tolerance."
    ),
    "inconclusive": (
        _INCONCLUSIVE + ": canary drift on {config} is {drift} against the "
        "±{threshold} tolerance (±{margin})."
    ),
}


@dataclass(frozen=True)
class CanaryObservation:
    """One canary run of one configuration: its pass rate and run date."""

    run_id: str
    date: str
    pass_at_1: float
    attempts: int


#: configuration label ("model [engine]") -> canary observations
CanaryHistory = Mapping[str, Sequence[CanaryObservation]]

_Context = tuple[str | None, str | None, str | None]


def conclusions(
    axis: ComparisonAxis,
    assigned: Sequence[tuple[int, ConfigurationStats]],
    *,
    models: Mapping[str, ModelConfig],
    canary_history: CanaryHistory | None = None,
) -> list[dict[str, object]]:
    """Evaluate every verdict rule of ``axis`` into conclusion callouts.

    ``assigned`` is the report's side assignment: (cohort index, configuration
    stats) pairs, exactly what ``compare_report.report_action`` builds.
    """

    callouts: list[dict[str, object]] = []
    for rule in axis.verdicts:
        if rule.kind == "canary_drift":
            callouts.extend(_drift_callouts(rule, canary_history or {}))
        elif rule.kind == "quality_bar":
            callouts.extend(_quality_bar_callouts(axis, rule, assigned))
        else:
            callouts.extend(_pair_callouts(axis, rule, assigned, models))
    return callouts


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _callout(
    rule: VerdictRule,
    status: str,
    text: str,
    run_ids: Sequence[str],
    context: _Context | None = None,
) -> dict[str, object]:
    return {
        "rule_id": rule.id,
        "status": status,
        "text": text,
        "metric": rule.metric,
        "threshold": rule.threshold,
        "margin": rule.margin,
        "unit": rule.unit,
        "run_ids": sorted(set(run_ids)),
        "context": None
        if context is None
        else {"suite": context[0], "suite_version": context[1], "hardware_tag": context[2]},
    }


def _template(rule: VerdictRule, status: str, defaults: Mapping[str, str]) -> str:
    return _TEMPLATES.get(rule.id, {}).get(status) or defaults[status]


def _metric_value(stat: ConfigurationStats, metric: str) -> float | None:
    if metric == "pass_at_1":
        return stat.pass_at_1 if stat.attempts else None
    if metric == "cost_per_task_usd":
        return stat.cost_per_task_usd if stat.attempts else None
    if metric == "memory_bytes":
        return float(stat.memory_bytes) if stat.memory_bytes is not None else None
    summaries = {
        "median_ttft_seconds": stat.ttft.median,
        "p95_ttft_seconds": stat.ttft.p95,
        "median_prefill_tokens_per_second": stat.prefill_tokens_per_second.median,
        "median_decode_tokens_per_second": stat.decode_tokens_per_second.median,
        "median_latency_seconds": stat.latency.median,
        "p95_latency_seconds": stat.latency.p95,
    }
    return summaries[metric]


def _config_label(stat: ConfigurationStats) -> str:
    return f"{stat.model} [{stat.engine_label}]"


def _context_of(stat: ConfigurationStats) -> _Context:
    return (stat.suite, stat.suite_version, stat.hardware_tag)


def _context_sort(context: _Context) -> tuple[str, str, str]:
    suite, suite_version, hardware_tag = context
    return (suite or "", suite_version or "", hardware_tag or "")


def _where(context: _Context) -> str:
    suite, suite_version, _hardware_tag = context
    if not suite:
        return ""
    return f" on {suite} v{suite_version}" if suite_version else f" on {suite}"


# ---------------------------------------------------------------------------
# pair rules: one cohort's aggregate thresholded against another's
# ---------------------------------------------------------------------------


def _pair_callouts(
    axis: ComparisonAxis,
    rule: VerdictRule,
    assigned: Sequence[tuple[int, ConfigurationStats]],
    models: Mapping[str, ModelConfig],
) -> list[dict[str, object]]:
    label, higher_better, fmt_metric = _METRICS[rule.metric]
    fmt_unit = _UNIT_FORMATS[rule.unit if rule.unit in _UNIT_FORMATS else None]
    index_by_name = {cohort.name: index for index, cohort in enumerate(axis.cohorts)}
    a_name, b_name = rule.sides or (axis.cohorts[0].name, axis.cohorts[1].name)

    members: dict[str, list[ConfigurationStats]] = {a_name: [], b_name: []}
    valued: dict[str, list[ConfigurationStats]] = {a_name: [], b_name: []}
    for side, stat in assigned:
        for name in (a_name, b_name):
            if side == index_by_name[name]:
                members[name].append(stat)
                if _metric_value(stat, rule.metric) is not None:
                    valued[name].append(stat)

    # One-sided or metric-less data: state what is missing, never conclude.
    problems = []
    for name in (a_name, b_name):
        if not members[name]:
            to_run = cohort_model_names(axis.cohorts[index_by_name[name]], models)
            problems.append(
                f"needs a {name} run of {', '.join(to_run)}"
                if to_run
                else f"no configured models match cohort '{name}'"
            )
        elif not valued[name]:
            problems.append(f"{name} has runs but no {label} samples")
    seen_runs = [run for name in (a_name, b_name) for s in members[name] for run in s.run_ids]
    if problems:
        text = f"No verdict on {label}: " + "; ".join(problems) + "."
        return [_callout(rule, "insufficient", text, seen_runs)]

    by_context: dict[str, dict[_Context, list[ConfigurationStats]]] = {
        name: _group_by_context(valued[name]) for name in (a_name, b_name)
    }
    shared = sorted(
        set(by_context[a_name]) & set(by_context[b_name]), key=_context_sort
    )
    if not shared:
        text = (
            f"No verdict on {label}: {a_name} and {b_name} have no shared "
            "(suite, suite version, hardware) context — never compared across contexts."
        )
        return [_callout(rule, "insufficient", text, seen_runs)]

    callouts = []
    for context in shared:
        a_best = _best(by_context[a_name][context], rule.metric, higher_better)
        b_best = _best(by_context[b_name][context], rule.metric, higher_better)
        a_val = _metric_value(a_best, rule.metric)
        b_val = _metric_value(b_best, rule.metric)
        assert a_val is not None and b_val is not None  # filtered above
        run_ids = [*a_best.run_ids, *b_best.run_ids]
        value = _compare_value(a_val, b_val, rule.unit)
        if value is None:
            text = f"No verdict on {label}{_where(context)}: {b_name} baseline is zero."
            callouts.append(_callout(rule, "insufficient", text, run_ids, context))
            continue
        fields = {
            "a_name": a_name,
            "b_name": b_name,
            "a_config": _config_label(a_best),
            "b_config": _config_label(b_best),
            "a_value": fmt_metric(a_val),
            "b_value": fmt_metric(b_val),
            "value": fmt_unit(value),
            "threshold": fmt_unit(rule.threshold),
            "margin": fmt_unit(rule.margin),
            "metric": label,
            "where": _where(context),
        }
        status = _status(value, rule)
        text = _template(rule, status, _DEFAULT_PAIR).format(**fields)
        callouts.append(_callout(rule, status, text, run_ids, context))
    return callouts


def _group_by_context(
    stats: Sequence[ConfigurationStats],
) -> dict[_Context, list[ConfigurationStats]]:
    grouped: dict[_Context, list[ConfigurationStats]] = defaultdict(list)
    for stat in stats:
        grouped[_context_of(stat)].append(stat)
    return grouped


def _best(
    stats: Sequence[ConfigurationStats], metric: str, higher_better: bool
) -> ConfigurationStats:
    values = {id(stat): _metric_value(stat, metric) for stat in stats}
    pick = max if higher_better else min
    return pick(stats, key=lambda stat: values[id(stat)])  # type: ignore[arg-type,return-value]


def _compare_value(a: float, b: float, unit: str | None) -> float | None:
    """The computed comparison value for a pair rule; ``None`` when undefined."""

    if unit == "ratio":
        return a / b if b > 0 else None
    if unit == "pp":
        return (a - b) * 100
    return a - b


def _status(value: float, rule: VerdictRule) -> str:
    if abs(value - rule.threshold) <= rule.margin:
        return "inconclusive"
    return "holds" if value >= rule.threshold else "fails"


# ---------------------------------------------------------------------------
# quality bar: the smallest configuration within N pp of the best
# ---------------------------------------------------------------------------


def _quality_bar_callouts(
    axis: ComparisonAxis,
    rule: VerdictRule,
    assigned: Sequence[tuple[int, ConfigurationStats]],
) -> list[dict[str, object]]:
    _label, _higher, fmt_metric = _METRICS["pass_at_1"]
    fmt_pp = _UNIT_FORMATS["pp"]
    valued = [(side, stat) for side, stat in assigned if stat.attempts]
    contexts = sorted(
        {_context_of(stat) for _side, stat in valued}, key=_context_sort
    )
    evaluable = [
        context
        for context in contexts
        if sum(1 for _side, stat in valued if _context_of(stat) == context) >= 2
    ]
    if not evaluable:
        text = (
            "No verdict on the quality bar: needs pass@1 runs of at least two "
            "ladder configurations on the same suite and hardware."
        )
        run_ids = [run for _side, stat in valued for run in stat.run_ids]
        return [_callout(rule, "insufficient", text, run_ids)]

    callouts = []
    for context in evaluable:
        rows = [(side, stat) for side, stat in valued if _context_of(stat) == context]
        best = max(rows, key=lambda pair: pair[1].pass_at_1)[1]

        def winner(bar_pp: float, rows=rows, best=best) -> tuple[int, ConfigurationStats]:
            qualifying = [
                (side, stat)
                for side, stat in rows
                if (best.pass_at_1 - stat.pass_at_1) * 100 <= bar_pp
            ]
            # Cohorts are declared smallest-first, so the lowest side index is
            # the smallest capability class that clears the bar.
            return min(qualifying, key=lambda pair: (pair[0], pair[1].model))

        run_ids = [run for _side, stat in rows for run in stat.run_ids]
        side, chosen = winner(rule.threshold)
        fields = {
            "config": _config_label(chosen),
            "side_name": axis.cohorts[side].name,
            "value": fmt_metric(chosen.pass_at_1),
            "gap": fmt_pp((best.pass_at_1 - chosen.pass_at_1) * 100),
            "best_config": _config_label(best),
            "best_value": fmt_metric(best.pass_at_1),
            "threshold": fmt_pp(rule.threshold),
            "margin": fmt_pp(rule.margin),
            "where": _where(context),
        }
        # Noise sensitivity: if wobbling the bar by the declared margin changes
        # the winner, the verdict is within noise — never state it confidently.
        if winner(rule.threshold - rule.margin) != winner(rule.threshold + rule.margin):
            text = _template(rule, "inconclusive", _DEFAULT_QUALITY_BAR).format(**fields)
            callouts.append(_callout(rule, "inconclusive", text, run_ids, context))
            continue
        text = _template(rule, "holds", _DEFAULT_QUALITY_BAR).format(**fields)
        callouts.append(_callout(rule, "holds", text, run_ids, context))
    return callouts


# ---------------------------------------------------------------------------
# canary drift: latest run vs the previous one, per configuration
# ---------------------------------------------------------------------------


def _drift_callouts(rule: VerdictRule, history: CanaryHistory) -> list[dict[str, object]]:
    fmt_metric = _METRICS["pass_at_1"][2]
    fmt_pp = _UNIT_FORMATS["pp"]
    if not history:
        text = (
            "No canary runs yet — run `--suite canary` per configuration to seed "
            "the drift check."
        )
        return [_callout(rule, "insufficient", text, [])]

    callouts = []
    for label in sorted(history):
        observations = sorted(history[label], key=lambda obs: (obs.date, obs.run_id))
        if len(observations) < 2:
            only = observations[0]
            text = (
                f"needs a second canary run of {label} to check drift "
                f"(only {fmt_metric(only.pass_at_1)} on {only.date} so far)."
            )
            callouts.append(_callout(rule, "insufficient", text, [only.run_id]))
            continue
        prev, latest = observations[-2], observations[-1]
        drift = (latest.pass_at_1 - prev.pass_at_1) * 100
        if abs(abs(drift) - rule.threshold) <= rule.margin:
            status = "inconclusive"
        elif abs(drift) > rule.threshold:
            status = "holds"
        else:
            status = "fails"
        fields = {
            "config": label,
            "latest_value": fmt_metric(latest.pass_at_1),
            "latest_date": latest.date,
            "prev_value": fmt_metric(prev.pass_at_1),
            "prev_date": prev.date,
            "drift": fmt_pp(drift),
            "threshold": fmt_pp(rule.threshold),
            "margin": fmt_pp(rule.margin),
        }
        text = _template(rule, status, _DEFAULT_DRIFT).format(**fields)
        callouts.append(_callout(rule, status, text, [prev.run_id, latest.run_id]))
    return callouts


# ---------------------------------------------------------------------------
# canary history from the raw result files
# ---------------------------------------------------------------------------


def canary_history(
    result_paths: Sequence[str | Path],
) -> dict[str, list[CanaryObservation]]:
    """Per-configuration canary pass rates over runs, sorted oldest first.

    Unlike the 17.1-001 aggregates — which pool a configuration's runs — drift
    needs one observation *per run*, so this reads the raw files directly:
    canary-suite endpoint records, deduped to the latest attempt per task
    within each run, dated from the run's metadata timestamp.
    """

    history: dict[str, dict[str, CanaryObservation]] = defaultdict(dict)
    for path in result_paths:
        file_path = Path(path)
        run_id = file_path.name
        records = _read_records(file_path)
        date = next(
            (
                str(record["timestamp"]).split("T", 1)[0]
                for record in records
                if record.get("record_type") == "metadata"
                and isinstance(record.get("timestamp"), str)
            ),
            "",
        )
        by_task: dict[tuple[str, str, str], bool] = {}
        for record in records:
            if record.get("record_type") in {"metadata", "power"}:
                continue
            if record.get("run_mode") != "endpoint" or record.get("suite") != "canary":
                continue
            model = record.get("model")
            task_id = record.get("task_id")
            if not isinstance(model, str) or not isinstance(task_id, str):
                continue
            engine = backend_label(record.get("engine"), record.get("endpoint_provider"))
            by_task[(model, engine, task_id)] = record.get("passed") is True
        attempts: dict[tuple[str, str], list[bool]] = defaultdict(list)
        for (model, engine, _task_id), passed in by_task.items():
            attempts[(model, engine)].append(passed)
        for (model, engine), outcomes in attempts.items():
            label = f"{model} [{engine}]"
            history[label][run_id] = CanaryObservation(
                run_id=run_id,
                date=date,
                pass_at_1=sum(outcomes) / len(outcomes),
                attempts=len(outcomes),
            )
    return {
        label: sorted(observations.values(), key=lambda obs: (obs.date, obs.run_id))
        for label, observations in sorted(history.items())
    }
