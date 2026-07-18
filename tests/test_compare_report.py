"""Tests for the report-style comparison view's server side (story 17.2-001).

Contract under test:

* The Benchmarks tab's axis picker is fed by ``axes_action``: every catalog
  axis is listed, data-ready axes first, empty ones marked with the models to
  run per cohort.
* ``report_action`` builds everything the report renders for one axis: hero
  sides, a subtitle stating the controlled variables, methodology chips from
  run metadata, per-member paired stats with side assignment and controlled
  badges, the cross-cutting Pareto frontier, and context-scaling series from
  sweep records.
* The comparison side colors are theme tokens (``--cmp-side-*``) resolving
  through the existing chart palette — no new raw color literals.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from local_code_bench import compare, compare_report, theme
from local_code_bench.config import (
    CohortFilter,
    ComparisonAxis,
    ComparisonCatalog,
    HighlightedPair,
    ModelConfig,
    TokenPrices,
)
from local_code_bench.dashboard_model import SweepPoint
from local_code_bench.results import append_jsonl


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _metadata(
    *,
    hardware_tag: str = "M3 Max 48 GB",
    timestamp: str = "2026-07-17T10:00:00+00:00",
    models: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "record_type": "metadata",
        "timestamp": timestamp,
        "seed": 0,
        "temperature": 0.0,
        "suite": "humaneval",
        "hardware_tag": hardware_tag,
        "models": models or {},
    }


def _endpoint_record(
    model: str,
    task_id: str,
    *,
    passed: bool = True,
    suite: str = "humaneval",
    suite_version: str = "1.0",
    ttft: float = 0.5,
    prefill: float = 100.0,
    decode: float = 40.0,
    latency: float = 2.0,
    cost_usd: float = 0.0,
    engine: dict[str, object] | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "run_mode": "endpoint",
        "model": model,
        "task_id": task_id,
        "suite": suite,
        "suite_version": suite_version,
        "passed": passed,
        "cost_usd": cost_usd,
        "metrics": {
            "ttft_seconds": ttft,
            "prefill_tokens_per_second": prefill,
            "decode_tokens_per_second": decode,
            "latency_seconds": latency,
        },
    }
    if engine is not None:
        record["engine"] = engine
    return record


def _engine(name: str, version: str = "1.0") -> dict[str, object]:
    return {"name": name, "versions": {name: version}, "capture_method": "live-api"}


def _write_run(
    path: Path,
    records: list[dict[str, object]],
    *,
    metadata: dict[str, object] | None = None,
) -> Path:
    append_jsonl(path, metadata if metadata is not None else _metadata())
    for record in records:
        append_jsonl(path, record)
    return path


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
        "local-mlx-beta": _model_cfg("local-mlx-beta", inferencer="mlx-lm"),
    }


def _engine_axis() -> ComparisonAxis:
    return ComparisonAxis(
        id="engine",
        title="Engine: mlx-lm vs Ollama",
        description="Same base model on both engines.",
        pairing_key="base_model",
        cohorts=(
            CohortFilter(name="mlx-lm", inferencer="mlx-lm"),
            CohortFilter(name="ollama", inferencer="ollama"),
        ),
        highlighted_pairs=(
            HighlightedPair(
                models=("local-mlx-alpha", "local-ollama-alpha"),
                reason="Identical published weights on both engines.",
            ),
        ),
    )


def _empty_axis() -> ComparisonAxis:
    return ComparisonAxis(
        id="quant",
        title="Quantization: q4 vs q8",
        pairing_key="base_model_engine",
        cohorts=(
            CohortFilter(name="q4", names=("local-mlx-gamma-4bit",)),
            CohortFilter(name="q8", names=("local-mlx-gamma-8bit",)),
        ),
    )


def _catalog() -> ComparisonCatalog:
    # Empty axis first: axes_action must reorder data-ready axes ahead of it.
    return ComparisonCatalog(axes=(_empty_axis(), _engine_axis()))


def _paired_stats(tmp_path: Path, **kwargs) -> tuple[compare.ConfigurationStats, ...]:
    run = _write_run(
        tmp_path / "run.jsonl",
        [
            _endpoint_record(
                "local-mlx-alpha", "t1", decode=50.0, prefill=200.0, engine=_engine("mlx_lm.server")
            ),
            _endpoint_record(
                "local-mlx-alpha", "t2", decode=50.0, passed=False, engine=_engine("mlx_lm.server")
            ),
            _endpoint_record(
                "local-ollama-alpha", "t1", decode=30.0, prefill=90.0, engine=_engine("ollama", "0.5")
            ),
        ],
        metadata=_metadata(
            models={
                "local-mlx-alpha": {"engine": _engine("mlx_lm.server")},
                "local-ollama-alpha": {"engine": _engine("ollama", "0.5")},
            }
        ),
    )
    return compare.build_configuration_stats([run], **kwargs)


# ---------------------------------------------------------------------------
# side-color tokens (AC4)
# ---------------------------------------------------------------------------


def test_comparison_side_tokens_defined_in_theme() -> None:
    for token in ("--cmp-side-1:", "--cmp-side-2:", "--cmp-side-3:", "--cmp-side-4:"):
        assert token in theme.TOKENS_CSS, f"missing side token {token}"


def test_comparison_side_tokens_resolve_through_existing_palette() -> None:
    # Side colors must be var() references, never fresh color literals.
    for line in theme.TOKENS_CSS.splitlines():
        if "--cmp-side-" in line:
            value = line.split(":", 1)[1]
            assert re.fullmatch(r"\s*var\(--[a-z0-9-]+\);\s*", value), line


# ---------------------------------------------------------------------------
# axes_action: the picker payload
# ---------------------------------------------------------------------------


def test_axes_action_lists_catalog_data_ready_first(tmp_path: Path) -> None:
    status, payload = compare_report.axes_action(_catalog(), _paired_stats(tmp_path), _registry())
    assert status == 200
    assert [axis["id"] for axis in payload["axes"]] == ["engine", "quant"]
    ready = {axis["id"]: axis["data_ready"] for axis in payload["axes"]}
    assert ready == {"engine": True, "quant": False}


def test_axes_action_marks_empty_axes_with_models_to_run(tmp_path: Path) -> None:
    registry = dict(_registry())
    registry["local-mlx-gamma-4bit"] = _model_cfg("local-mlx-gamma-4bit", inferencer="mlx-lm")
    _status, payload = compare_report.axes_action(_catalog(), _paired_stats(tmp_path), registry)
    quant_axis = next(axis for axis in payload["axes"] if axis["id"] == "quant")
    assert quant_axis["data_ready"] is False
    by_name = {cohort["name"]: cohort for cohort in quant_axis["cohorts"]}
    assert by_name["q4"]["models_to_run"] == ["local-mlx-gamma-4bit"]
    assert by_name["q4"]["matched"] == []


def test_axes_action_surfaces_catalog_errors() -> None:
    catalog = ComparisonCatalog(axes=(), errors=("comparisons[0].id is required",))
    _status, payload = compare_report.axes_action(catalog, (), {})
    assert payload["axes"] == []
    assert payload["errors"] == ["comparisons[0].id is required"]


# ---------------------------------------------------------------------------
# report_action: the per-axis report payload
# ---------------------------------------------------------------------------


def test_report_unknown_axis_is_404(tmp_path: Path) -> None:
    status, payload = compare_report.report_action(
        _catalog(), _paired_stats(tmp_path), "bogus", models=_registry()
    )
    assert status == 404
    assert "bogus" in payload["error"]
    assert payload["axes"] == ["quant", "engine"]


def test_report_hero_sides_and_member_side_assignment(tmp_path: Path) -> None:
    status, payload = compare_report.report_action(
        _catalog(), _paired_stats(tmp_path), "engine", models=_registry()
    )
    assert status == 200
    assert payload["axis"]["title"] == "Engine: mlx-lm vs Ollama"
    assert [side["name"] for side in payload["sides"]] == ["mlx-lm", "ollama"]
    members = {member["model"]: member for member in payload["members"]}
    assert members["local-mlx-alpha"]["side"] == 0
    assert members["local-ollama-alpha"]["side"] == 1


def test_report_members_carry_paired_stats(tmp_path: Path) -> None:
    _status, payload = compare_report.report_action(
        _catalog(), _paired_stats(tmp_path), "engine", models=_registry()
    )
    member = next(m for m in payload["members"] if m["model"] == "local-mlx-alpha")
    stats = member["stats"]
    assert stats["pass_at_1"] == 0.5
    assert stats["decode_tokens_per_second"] == 50.0
    assert stats["prefill_tokens_per_second"] == 150.0  # median of 200 and 100
    assert stats["ttft_seconds"] == 0.5
    assert stats["cost_per_task_usd"] == 0.0
    assert stats["attempts"] == 2


def test_report_controlled_pair_is_badged(tmp_path: Path) -> None:
    _status, payload = compare_report.report_action(
        _catalog(), _paired_stats(tmp_path), "engine", models=_registry()
    )
    members = {member["model"]: member for member in payload["members"]}
    assert (
        members["local-mlx-alpha"]["controlled"]["reason"]
        == "Identical published weights on both engines."
    )
    assert members["local-ollama-alpha"]["controlled"] is not None


def test_report_controlled_badge_needs_a_present_partner(tmp_path: Path) -> None:
    run = _write_run(
        tmp_path / "solo.jsonl",
        [_endpoint_record("local-mlx-alpha", "t1")],
    )
    stats = compare.build_configuration_stats([run])
    _status, payload = compare_report.report_action(
        _catalog(), stats, "engine", models=_registry()
    )
    member = next(m for m in payload["members"] if m["model"] == "local-mlx-alpha")
    assert member["controlled"] is None


def test_report_methodology_chips_from_run_metadata(tmp_path: Path) -> None:
    run = _write_run(
        tmp_path / "run.jsonl",
        [
            _endpoint_record("local-mlx-alpha", "t1", engine=_engine("mlx_lm.server", "2.3")),
            _endpoint_record("local-ollama-alpha", "t1", engine=_engine("ollama", "0.5")),
        ],
        metadata=_metadata(
            timestamp="2026-07-16T09:00:00+00:00",
            models={
                "local-mlx-alpha": {"engine": _engine("mlx_lm.server", "2.3")},
                "local-ollama-alpha": {"engine": _engine("ollama", "0.5")},
            },
        ),
    )
    stats = compare.build_configuration_stats([run])
    _status, payload = compare_report.report_action(
        _catalog(),
        stats,
        "engine",
        models=_registry(),
        metadata_by_run=compare_report.read_run_metadata([run]),
    )
    chips = {(chip["label"], chip["value"]) for chip in payload["chips"]}
    assert ("engine", "mlx_lm.server 2.3") in chips
    assert ("engine", "ollama 0.5") in chips
    assert ("suite", "humaneval v1.0") in chips
    assert ("seed / temp", "seed 0 · temp 0.0") in chips
    assert ("hardware", "M3 Max 48 GB") in chips
    assert ("run dates", "2026-07-16") in chips


def test_report_run_dates_chip_collapses_to_a_range(tmp_path: Path) -> None:
    run_a = _write_run(
        tmp_path / "a.jsonl",
        [_endpoint_record("local-mlx-alpha", "t1")],
        metadata=_metadata(timestamp="2026-07-15T09:00:00+00:00"),
    )
    run_b = _write_run(
        tmp_path / "b.jsonl",
        [_endpoint_record("local-ollama-alpha", "t1")],
        metadata=_metadata(timestamp="2026-07-17T21:00:00+00:00"),
    )
    stats = compare.build_configuration_stats([run_a, run_b])
    _status, payload = compare_report.report_action(
        _catalog(),
        stats,
        "engine",
        models=_registry(),
        metadata_by_run=compare_report.read_run_metadata([run_a, run_b]),
    )
    chips = {chip["label"]: chip["value"] for chip in payload["chips"] if chip["label"] == "run dates"}
    assert chips["run dates"] == "2026-07-15 – 2026-07-17"


def test_report_subtitle_states_controlled_variables(tmp_path: Path) -> None:
    _status, payload = compare_report.report_action(
        _catalog(),
        _paired_stats(tmp_path),
        "engine",
        models=_registry(),
        metadata_by_run={},
    )
    subtitle = payload["subtitle"]
    assert "humaneval v1.0" in subtitle
    assert "M3 Max 48 GB" in subtitle


# ---------------------------------------------------------------------------
# Pareto frontier (cross-cutting)
# ---------------------------------------------------------------------------


def test_pareto_frontier_marks_non_dominated_points() -> None:
    points = [(10.0, 0.9), (50.0, 0.5), (30.0, 0.7), (20.0, 0.6), (5.0, 0.8)]
    frontier = compare_report.pareto_frontier(points)
    # (20, 0.6) is dominated by (30, 0.7); (5, 0.8) by (10, 0.9).
    assert frontier == {0, 1, 2}


def test_report_frontier_is_cross_cutting_with_side_marks(tmp_path: Path) -> None:
    run = _write_run(
        tmp_path / "run.jsonl",
        [
            _endpoint_record("local-mlx-alpha", "t1", decode=50.0),
            _endpoint_record("local-ollama-alpha", "t1", decode=30.0, passed=False),
            # Not in any engine cohort (no registry entry): still on the chart.
            _endpoint_record("cloud-frontier", "t1", decode=80.0),
        ],
    )
    stats = compare.build_configuration_stats(
        [run], memory={"local-mlx-alpha": {None: 12_000_000_000}}
    )
    _status, payload = compare_report.report_action(
        _catalog(), stats, "engine", models=_registry()
    )
    points = {point["model"]: point for point in payload["frontier"]}
    assert set(points) == {"local-mlx-alpha", "local-ollama-alpha", "cloud-frontier"}
    assert points["cloud-frontier"]["side"] is None
    assert points["local-mlx-alpha"]["side"] == 0
    assert points["local-mlx-alpha"]["memory_bytes"] == 12_000_000_000
    assert points["cloud-frontier"]["frontier"] is True
    assert points["local-ollama-alpha"]["frontier"] is False


# ---------------------------------------------------------------------------
# context-scaling series
# ---------------------------------------------------------------------------


def test_report_context_scaling_series_from_sweep_points(tmp_path: Path) -> None:
    sweep = (
        SweepPoint(
            model="local-mlx-alpha",
            context_tokens=4096,
            ttft_seconds=1.0,
            prefill_tokens_per_second=200.0,
            engine_label="mlx_lm.server",
        ),
        SweepPoint(
            model="local-mlx-alpha",
            context_tokens=1024,
            ttft_seconds=0.4,
            prefill_tokens_per_second=300.0,
            engine_label="mlx_lm.server",
        ),
    )
    _status, payload = compare_report.report_action(
        _catalog(), _paired_stats(tmp_path), "engine", models=_registry(), sweep_points=sweep
    )
    series = payload["context_scaling"]
    assert len(series) == 1
    assert series[0]["side"] == 0
    assert [point["context_tokens"] for point in series[0]["points"]] == [1024, 4096]


def test_report_without_sweep_data_has_empty_context_scaling(tmp_path: Path) -> None:
    _status, payload = compare_report.report_action(
        _catalog(), _paired_stats(tmp_path), "engine", models=_registry()
    )
    assert payload["context_scaling"] == []


# ---------------------------------------------------------------------------
# empty axis + degradation + serializability
# ---------------------------------------------------------------------------


def test_report_empty_axis_lists_models_to_run(tmp_path: Path) -> None:
    registry = dict(_registry())
    registry["local-mlx-gamma-4bit"] = _model_cfg("local-mlx-gamma-4bit", inferencer="mlx-lm")
    status, payload = compare_report.report_action(
        _catalog(), _paired_stats(tmp_path), "quant", models=registry
    )
    assert status == 200
    assert payload["data_ready"] is False
    assert payload["members"] == []
    by_name = {side["name"]: side for side in payload["sides"]}
    assert by_name["q4"]["models_to_run"] == ["local-mlx-gamma-4bit"]


def test_load_catalog_safe_degrades_to_errors(tmp_path: Path) -> None:
    catalog = compare_report.load_catalog_safe(tmp_path / "missing.yaml")
    assert catalog.axes == ()
    assert len(catalog.errors) == 1
    assert "missing.yaml" in catalog.errors[0]


def test_load_catalog_safe_reads_the_shipped_catalog() -> None:
    catalog = compare_report.load_catalog_safe("configs/comparisons.yaml")
    assert catalog.errors == ()
    assert any(axis.id == "engine" for axis in catalog.axes)


def test_report_payload_is_json_serializable(tmp_path: Path) -> None:
    _status, payload = compare_report.report_action(
        _catalog(),
        _paired_stats(tmp_path),
        "engine",
        models=_registry(),
        metadata_by_run=compare_report.read_run_metadata(list(tmp_path.glob("*.jsonl"))),
    )
    json.dumps(payload)
