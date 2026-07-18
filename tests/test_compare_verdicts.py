"""Tests for the deterministic conclusion callouts (story 17.2-002).

Contract under test:

* ``conclusions`` evaluates each of an axis's declared verdict rules as a pure
  function over the 17.1-001 aggregates and renders templated prose with the
  computed numbers inline; every callout lists its supporting run IDs and the
  threshold it applied.
* Insufficient or one-sided data produces a callout that states what is
  missing ("needs a q8 run of X") instead of concluding from partial data.
* A value within a rule's declared noise margin of its threshold is phrased
  "inconclusive — within noise margin", never as a confident verdict.
* ``canary_history`` + a ``canary_drift`` rule call out drift beyond the
  declared tolerance versus the previous run, with both values and dates.
"""

from __future__ import annotations

import json
from pathlib import Path

from local_code_bench import compare, compare_verdicts
from local_code_bench.config import (
    CohortFilter,
    ComparisonAxis,
    ModelConfig,
    TokenPrices,
    VerdictRule,
)
from local_code_bench.results import append_jsonl


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _model_cfg(
    name: str, *, inferencer: str | None = None, quant: str | None = None
) -> ModelConfig:
    return ModelConfig(
        name=name,
        type="openai",
        base_url="http://127.0.0.1:8000/v1",
        model_id=f"{name}-id",
        pinned_revision="main",
        price_per_1k_tokens=TokenPrices(input=0.0, output=0.0),
        inferencer=inferencer,
        quant=quant,
    )


def _registry() -> dict[str, ModelConfig]:
    return {
        "local-mlx-alpha": _model_cfg("local-mlx-alpha", inferencer="mlx-lm"),
        "local-ollama-alpha": _model_cfg("local-ollama-alpha", inferencer="ollama"),
    }


def _stat(
    model: str,
    engine: str,
    *,
    pass_at_1: float = 0.5,
    attempts: int = 4,
    prefill: float | None = 100.0,
    decode: float | None = 40.0,
    ttft: float | None = 0.5,
    latency: float | None = 2.0,
    cost: float = 0.0,
    suite: str | None = "humaneval",
    suite_version: str | None = "1.0",
    hardware_tag: str | None = "M3 Max 48 GB",
    run_ids: tuple[str, ...] = ("run.jsonl",),
) -> compare.ConfigurationStats:
    def summary(value: float | None) -> compare.MetricSummary:
        if value is None:
            return compare.MetricSummary(median=None, p95=None, samples=0)
        return compare.MetricSummary(median=value, p95=value, samples=attempts)

    return compare.ConfigurationStats(
        model=model,
        engine_label=engine,
        quant=None,
        base_model_key=model,
        suite=suite,
        suite_version=suite_version,
        hardware_tag=hardware_tag,
        run_ids=run_ids,
        attempts=attempts,
        passed=round(pass_at_1 * attempts),
        pass_at_1=pass_at_1,
        ttft=summary(ttft),
        prefill_tokens_per_second=summary(prefill),
        decode_tokens_per_second=summary(decode),
        latency=summary(latency),
        cost_per_task_usd=cost,
        memory_bytes=None,
    )


def _axis(*rules: VerdictRule) -> ComparisonAxis:
    return ComparisonAxis(
        id="engine",
        title="Engine: mlx-lm vs Ollama",
        pairing_key="base_model",
        cohorts=(
            CohortFilter(name="mlx-lm", inferencer="mlx-lm"),
            CohortFilter(name="ollama", inferencer="ollama"),
        ),
        verdicts=rules,
    )


def _prefill_rule(**overrides: object) -> VerdictRule:
    params: dict = {
        "id": "prefill-advantage",
        "metric": "median_prefill_tokens_per_second",
        "threshold": 1.10,
        "unit": "ratio",
    }
    params.update(overrides)
    return VerdictRule(**params)


def _assigned(
    *stats: compare.ConfigurationStats,
) -> list[tuple[int, compare.ConfigurationStats]]:
    side_by_engine = {"mlx_lm.server": 0, "ollama": 1}
    return [(side_by_engine[stat.engine_label], stat) for stat in stats]


# ---------------------------------------------------------------------------
# pair rules: holds / fails / inconclusive with numbers, run IDs, threshold
# ---------------------------------------------------------------------------


def test_pair_rule_holds_with_numbers_run_ids_and_threshold() -> None:
    assigned = _assigned(
        _stat("local-mlx-alpha", "mlx_lm.server", prefill=220.0, run_ids=("a.jsonl",)),
        _stat("local-ollama-alpha", "ollama", prefill=100.0, run_ids=("b.jsonl",)),
    )
    callouts = compare_verdicts.conclusions(_axis(_prefill_rule()), assigned, models=_registry())

    assert len(callouts) == 1
    callout = callouts[0]
    assert callout["rule_id"] == "prefill-advantage"
    assert callout["status"] == "holds"
    assert "2.20×" in callout["text"]
    assert "220.0" in callout["text"] and "100.0" in callout["text"]
    assert "1.10×" in callout["text"]  # the threshold it applied, inline
    assert callout["threshold"] == 1.10
    assert sorted(callout["run_ids"]) == ["a.jsonl", "b.jsonl"]


def test_pair_rule_fails_below_threshold() -> None:
    assigned = _assigned(
        _stat("local-mlx-alpha", "mlx_lm.server", prefill=90.0),
        _stat("local-ollama-alpha", "ollama", prefill=100.0),
    )
    callouts = compare_verdicts.conclusions(_axis(_prefill_rule()), assigned, models=_registry())

    assert callouts[0]["status"] == "fails"
    assert "0.90×" in callouts[0]["text"]
    assert "1.10×" in callouts[0]["text"]


def test_pair_rule_within_margin_is_inconclusive_never_confident() -> None:
    # 115 / 100 = 1.15 — within the declared ±0.10 of the 1.10 threshold.
    assigned = _assigned(
        _stat("local-mlx-alpha", "mlx_lm.server", prefill=115.0),
        _stat("local-ollama-alpha", "ollama", prefill=100.0),
    )
    callouts = compare_verdicts.conclusions(
        _axis(_prefill_rule(margin=0.10)), assigned, models=_registry()
    )

    assert callouts[0]["status"] == "inconclusive"
    assert "inconclusive — within noise margin" in callouts[0]["text"]


def test_pair_rule_outside_margin_still_concludes() -> None:
    assigned = _assigned(
        _stat("local-mlx-alpha", "mlx_lm.server", prefill=220.0),
        _stat("local-ollama-alpha", "ollama", prefill=100.0),
    )
    callouts = compare_verdicts.conclusions(
        _axis(_prefill_rule(margin=0.10)), assigned, models=_registry()
    )

    assert callouts[0]["status"] == "holds"


def test_pair_rule_pp_unit_compares_percentage_points() -> None:
    rule = VerdictRule(id="q8-quality-gain", metric="pass_at_1", threshold=1.0, unit="pp")
    assigned = _assigned(
        _stat("local-mlx-alpha", "mlx_lm.server", pass_at_1=0.80),
        _stat("local-ollama-alpha", "ollama", pass_at_1=0.75),
    )
    callouts = compare_verdicts.conclusions(_axis(rule), assigned, models=_registry())

    assert callouts[0]["status"] == "holds"
    assert "5.0pp" in callouts[0]["text"]
    assert "80.0%" in callouts[0]["text"] and "75.0%" in callouts[0]["text"]


def test_pair_rule_declared_sides_orient_the_comparison() -> None:
    # sides reversed: the ratio is ollama/mlx even though mlx is cohort 0.
    rule = _prefill_rule(sides=("ollama", "mlx-lm"))
    assigned = _assigned(
        _stat("local-mlx-alpha", "mlx_lm.server", prefill=200.0),
        _stat("local-ollama-alpha", "ollama", prefill=100.0),
    )
    callouts = compare_verdicts.conclusions(_axis(rule), assigned, models=_registry())

    assert callouts[0]["status"] == "fails"
    assert "0.50×" in callouts[0]["text"]


def test_pair_rule_picks_best_configuration_per_side() -> None:
    assigned = _assigned(
        _stat("local-mlx-alpha", "mlx_lm.server", prefill=90.0),
        _stat("local-mlx-alpha", "mlx_lm.server", prefill=220.0, suite="humaneval"),
        _stat("local-ollama-alpha", "ollama", prefill=100.0),
    )
    callouts = compare_verdicts.conclusions(_axis(_prefill_rule()), assigned, models=_registry())

    assert callouts[0]["status"] == "holds"
    assert "2.20×" in callouts[0]["text"]


def test_pair_rule_evaluates_per_shared_context() -> None:
    # Two contexts each holding a pair: one callout per context, never pooled.
    assigned = _assigned(
        _stat("local-mlx-alpha", "mlx_lm.server", prefill=220.0, suite="humaneval"),
        _stat("local-ollama-alpha", "ollama", prefill=100.0, suite="humaneval"),
        _stat("local-mlx-alpha", "mlx_lm.server", prefill=90.0, suite="mbpp"),
        _stat("local-ollama-alpha", "ollama", prefill=100.0, suite="mbpp"),
    )
    callouts = compare_verdicts.conclusions(_axis(_prefill_rule()), assigned, models=_registry())

    assert [c["status"] for c in callouts] == ["holds", "fails"]
    assert callouts[0]["context"]["suite"] == "humaneval"
    assert callouts[1]["context"]["suite"] == "mbpp"


# ---------------------------------------------------------------------------
# insufficient / one-sided data: state what is missing, never conclude
# ---------------------------------------------------------------------------


def test_one_sided_data_names_the_missing_runs() -> None:
    assigned = _assigned(_stat("local-mlx-alpha", "mlx_lm.server", prefill=220.0))
    callouts = compare_verdicts.conclusions(_axis(_prefill_rule()), assigned, models=_registry())

    assert callouts[0]["status"] == "insufficient"
    assert "needs a ollama run of local-ollama-alpha" in callouts[0]["text"]


def test_no_data_at_all_names_both_missing_sides() -> None:
    callouts = compare_verdicts.conclusions(_axis(_prefill_rule()), [], models=_registry())

    assert callouts[0]["status"] == "insufficient"
    assert "mlx-lm" in callouts[0]["text"] and "ollama" in callouts[0]["text"]


def test_runs_without_the_metric_are_called_out_not_concluded() -> None:
    assigned = _assigned(
        _stat("local-mlx-alpha", "mlx_lm.server", prefill=None),
        _stat("local-ollama-alpha", "ollama", prefill=100.0),
    )
    callouts = compare_verdicts.conclusions(_axis(_prefill_rule()), assigned, models=_registry())

    assert callouts[0]["status"] == "insufficient"
    assert "no median prefill tok/s samples" in callouts[0]["text"]


def test_sides_without_a_shared_context_do_not_conclude() -> None:
    assigned = _assigned(
        _stat("local-mlx-alpha", "mlx_lm.server", prefill=220.0, suite="humaneval"),
        _stat("local-ollama-alpha", "ollama", prefill=100.0, suite="mbpp"),
    )
    callouts = compare_verdicts.conclusions(_axis(_prefill_rule()), assigned, models=_registry())

    assert callouts[0]["status"] == "insufficient"
    assert "no shared" in callouts[0]["text"]


# ---------------------------------------------------------------------------
# quality-bar rule (smallest configuration within N pp of the best)
# ---------------------------------------------------------------------------


def _ladder_axis(rule: VerdictRule) -> ComparisonAxis:
    return ComparisonAxis(
        id="size-ladder",
        title="Size ladder",
        pairing_key="suite_context",
        cohorts=(
            CohortFilter(name="small", names=("small-model",)),
            CohortFilter(name="large", names=("large-model",)),
        ),
        verdicts=(rule,),
    )


def _quality_rule(**overrides: object) -> VerdictRule:
    params: dict = {
        "id": "quality-bar",
        "metric": "pass_at_1",
        "threshold": 5.0,
        "unit": "pp",
        "kind": "quality_bar",
    }
    params.update(overrides)
    return VerdictRule(**params)


def test_quality_bar_names_smallest_configuration_clearing_the_bar() -> None:
    assigned = [
        (0, _stat("small-model", "mlx_lm.server", pass_at_1=0.82, run_ids=("s.jsonl",))),
        (1, _stat("large-model", "mlx_lm.server", pass_at_1=0.85, run_ids=("l.jsonl",))),
    ]
    callouts = compare_verdicts.conclusions(
        _ladder_axis(_quality_rule()), assigned, models={}
    )

    callout = callouts[0]
    assert callout["status"] == "holds"
    assert "small-model" in callout["text"]
    assert "82.0%" in callout["text"] and "85.0%" in callout["text"]
    assert "5.0pp" in callout["text"]
    assert sorted(callout["run_ids"]) == ["l.jsonl", "s.jsonl"]


def test_quality_bar_near_the_bar_is_inconclusive() -> None:
    # small trails by 4.5pp: inside the bar at 5.0 but within the ±1.0 margin,
    # so the winner flips with noise — never a confident verdict.
    assigned = [
        (0, _stat("small-model", "mlx_lm.server", pass_at_1=0.805)),
        (1, _stat("large-model", "mlx_lm.server", pass_at_1=0.85)),
    ]
    callouts = compare_verdicts.conclusions(
        _ladder_axis(_quality_rule(margin=1.0)), assigned, models={}
    )

    assert callouts[0]["status"] == "inconclusive"
    assert "inconclusive — within noise margin" in callouts[0]["text"]


def test_quality_bar_without_enough_data_is_insufficient() -> None:
    assigned = [(0, _stat("small-model", "mlx_lm.server", pass_at_1=0.82, attempts=4))]
    callouts = compare_verdicts.conclusions(
        _ladder_axis(_quality_rule()), assigned, models={}
    )

    assert callouts[0]["status"] == "insufficient"


# ---------------------------------------------------------------------------
# canary drift vs the previous run
# ---------------------------------------------------------------------------


def _canary_axis(rule: VerdictRule) -> ComparisonAxis:
    return ComparisonAxis(
        id="canary",
        title="Canary drift",
        pairing_key="suite_context",
        cohorts=(
            CohortFilter(name="mlx-lm", inferencer="mlx-lm"),
            CohortFilter(name="ollama", inferencer="ollama"),
        ),
        verdicts=(rule,),
    )


def _drift_rule(**overrides: object) -> VerdictRule:
    params: dict = {
        "id": "canary-drift",
        "metric": "pass_at_1",
        "threshold": 5.0,
        "unit": "pp",
        "kind": "canary_drift",
    }
    params.update(overrides)
    return VerdictRule(**params)


def _history(
    *observations: tuple[str, str, float],
) -> dict[str, list[compare_verdicts.CanaryObservation]]:
    history: dict[str, list[compare_verdicts.CanaryObservation]] = {}
    for run_id, date, pass_at_1 in observations:
        history.setdefault("local-mlx-alpha [mlx_lm.server]", []).append(
            compare_verdicts.CanaryObservation(
                run_id=run_id, date=date, pass_at_1=pass_at_1, attempts=10
            )
        )
    return history


def test_canary_drift_beyond_tolerance_reports_both_values_and_dates() -> None:
    history = _history(
        ("old.jsonl", "2026-07-01", 0.90),
        ("new.jsonl", "2026-07-15", 0.70),
    )
    callouts = compare_verdicts.conclusions(
        _canary_axis(_drift_rule()), [], models={}, canary_history=history
    )

    callout = callouts[0]
    assert callout["status"] == "holds"
    assert "90.0%" in callout["text"] and "70.0%" in callout["text"]
    assert "2026-07-01" in callout["text"] and "2026-07-15" in callout["text"]
    assert "5.0pp" in callout["text"]
    assert sorted(callout["run_ids"]) == ["new.jsonl", "old.jsonl"]


def test_canary_within_tolerance_reports_steady() -> None:
    history = _history(
        ("old.jsonl", "2026-07-01", 0.90),
        ("new.jsonl", "2026-07-15", 0.88),
    )
    callouts = compare_verdicts.conclusions(
        _canary_axis(_drift_rule()), [], models={}, canary_history=history
    )

    assert callouts[0]["status"] == "fails"
    assert "2026-07-01" in callouts[0]["text"] and "2026-07-15" in callouts[0]["text"]


def test_canary_drift_near_tolerance_is_inconclusive() -> None:
    # 5.5pp drift against a 5.0pp tolerance with a ±1.0pp margin: noise.
    history = _history(
        ("old.jsonl", "2026-07-01", 0.90),
        ("new.jsonl", "2026-07-15", 0.845),
    )
    callouts = compare_verdicts.conclusions(
        _canary_axis(_drift_rule(margin=1.0)), [], models={}, canary_history=history
    )

    assert callouts[0]["status"] == "inconclusive"
    assert "inconclusive — within noise margin" in callouts[0]["text"]


def test_canary_uses_latest_two_runs() -> None:
    history = _history(
        ("a.jsonl", "2026-06-01", 0.50),
        ("b.jsonl", "2026-07-01", 0.90),
        ("c.jsonl", "2026-07-15", 0.88),
    )
    callouts = compare_verdicts.conclusions(
        _canary_axis(_drift_rule()), [], models={}, canary_history=history
    )

    assert callouts[0]["status"] == "fails"
    assert sorted(callouts[0]["run_ids"]) == ["b.jsonl", "c.jsonl"]


def test_canary_single_run_needs_a_second() -> None:
    history = _history(("only.jsonl", "2026-07-15", 0.90))
    callouts = compare_verdicts.conclusions(
        _canary_axis(_drift_rule()), [], models={}, canary_history=history
    )

    assert callouts[0]["status"] == "insufficient"
    assert "needs a second canary run of local-mlx-alpha [mlx_lm.server]" in callouts[0]["text"]


def test_canary_without_any_runs_says_what_to_run() -> None:
    callouts = compare_verdicts.conclusions(
        _canary_axis(_drift_rule()), [], models={}, canary_history={}
    )

    assert callouts[0]["status"] == "insufficient"
    assert "--suite canary" in callouts[0]["text"]


# ---------------------------------------------------------------------------
# canary history extraction from raw result files
# ---------------------------------------------------------------------------


def _write_canary_run(
    path: Path, timestamp: str, passes: list[bool], *, suite: str = "canary"
) -> Path:
    append_jsonl(
        path,
        {
            "record_type": "metadata",
            "timestamp": timestamp,
            "seed": 0,
            "temperature": 0.0,
            "suite": suite,
            "hardware_tag": "M3 Max 48 GB",
        },
    )
    for index, passed in enumerate(passes):
        append_jsonl(
            path,
            {
                "run_mode": "endpoint",
                "model": "local-mlx-alpha",
                "task_id": f"t{index}",
                "suite": suite,
                "passed": passed,
                "engine": {
                    "name": "mlx_lm.server",
                    "versions": {"mlx_lm.server": "1.0"},
                    "capture_method": "live-api",
                },
                "metrics": {},
            },
        )
    return path


def test_canary_history_extracts_per_run_pass_rates_sorted_by_date(tmp_path: Path) -> None:
    new = _write_canary_run(tmp_path / "new.jsonl", "2026-07-15T10:00:00+00:00", [True, False])
    old = _write_canary_run(tmp_path / "old.jsonl", "2026-07-01T10:00:00+00:00", [True, True])

    history = compare_verdicts.canary_history([new, old])

    # Labelled like the 17.1-001 configuration stats: model [engine version].
    observations = history["local-mlx-alpha [mlx_lm.server 1.0]"]
    assert [obs.run_id for obs in observations] == ["old.jsonl", "new.jsonl"]
    assert [obs.date for obs in observations] == ["2026-07-01", "2026-07-15"]
    assert observations[0].pass_at_1 == 1.0
    assert observations[1].pass_at_1 == 0.5
    assert observations[1].attempts == 2


def test_canary_history_ignores_non_canary_suites(tmp_path: Path) -> None:
    run = _write_canary_run(
        tmp_path / "run.jsonl", "2026-07-15T10:00:00+00:00", [True], suite="humaneval"
    )

    assert compare_verdicts.canary_history([run]) == {}


# ---------------------------------------------------------------------------
# payload shape
# ---------------------------------------------------------------------------


def test_callouts_are_json_serializable_with_threshold_fields() -> None:
    assigned = _assigned(
        _stat("local-mlx-alpha", "mlx_lm.server", prefill=220.0),
        _stat("local-ollama-alpha", "ollama", prefill=100.0),
    )
    callouts = compare_verdicts.conclusions(
        _axis(_prefill_rule(margin=0.05)), assigned, models=_registry()
    )

    json.dumps(callouts)
    callout = callouts[0]
    assert callout["metric"] == "median_prefill_tokens_per_second"
    assert callout["threshold"] == 1.10
    assert callout["margin"] == 0.05
    assert callout["unit"] == "ratio"
