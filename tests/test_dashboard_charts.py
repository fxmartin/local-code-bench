from __future__ import annotations

import re

from local_code_bench.dashboard_charts import (
    ChartPoint,
    OmittedPoint,
    _scale,
    cost_quality_points,
    quality_speed_points,
    render_charts_section,
    sweep_series,
)
from local_code_bench.dashboard_model import (
    EndpointModelAggregate,
    SweepPoint,
    build_dashboard_data,
)


def _endpoint_record(model, task_id, passed, cost, prefill=None, suite="humaneval"):
    metrics = {"latency_seconds": 1.0}
    if prefill is not None:
        metrics["prefill_tokens_per_second"] = prefill
    return {
        "run_mode": "endpoint",
        "model": model,
        "suite": suite,
        "task_id": task_id,
        "passed": passed,
        "cost_usd": cost,
        "metrics": metrics,
    }


def _sweep_record(model, context, prefill):
    metrics = {"ttft_seconds": 1.0}
    if prefill is not None:
        metrics["prefill_tokens_per_second"] = prefill
    return {
        "run_mode": "sweep",
        "model": model,
        "context_tokens": context,
        "metrics": metrics,
    }


def _models(records):
    return build_dashboard_data(records).endpoint_models


def _sweeps(records):
    return build_dashboard_data(records).sweep_points


def test_cost_quality_points_maps_mean_cost_and_pass_rate() -> None:
    models = _models(
        [
            _endpoint_record("m1", "t0", True, 0.01),
            _endpoint_record("m1", "t1", False, 0.03),
        ]
    )

    points, omitted = cost_quality_points(models)

    assert omitted == []
    assert len(points) == 1
    point = points[0]
    assert isinstance(point, ChartPoint)
    assert "m1" in point.label
    assert point.x == 0.02  # mean of 0.01 and 0.03
    assert point.y == 0.5  # one of two passed


def test_quality_speed_omits_model_without_prefill_throughput() -> None:
    models = _models(
        [
            _endpoint_record("fast", "t0", True, 0.01, prefill=200.0),
            _endpoint_record("nospeed", "t0", True, 0.01, prefill=None),
        ]
    )

    points, omitted = quality_speed_points(models)

    labels = {point.label for point in points}
    assert any("fast" in label for label in labels)
    assert all("nospeed" not in label for label in labels)
    assert len(omitted) == 1
    assert isinstance(omitted[0], OmittedPoint)
    assert "nospeed" in omitted[0].label
    assert omitted[0].reason


def test_sweep_series_groups_by_model_and_omits_missing_throughput() -> None:
    points = _sweeps(
        [
            _sweep_record("m1", 1000, 180.0),
            _sweep_record("m1", 4000, 120.0),
            _sweep_record("m1", 8000, None),
        ]
    )

    series, omitted = sweep_series(points)

    assert set(series) == {"m1"}
    xs = [point.x for point in series["m1"]]
    assert xs == [1000.0, 4000.0]  # sorted by context, missing one dropped
    assert len(omitted) == 1
    assert "8,000" in omitted[0].label


def test_render_charts_section_contains_offline_svg_with_titles() -> None:
    models = _models(
        [
            _endpoint_record("m1", "t0", True, 0.01, prefill=200.0),
            _endpoint_record("m1", "t1", False, 0.02, prefill=100.0),
        ]
    )
    sweeps = _sweeps([_sweep_record("m1", 2000, 180.0)])

    html_out = render_charts_section(models, sweeps)

    assert "Cost vs Quality" in html_out
    assert "Quality vs Speed" in html_out
    assert "Sweep" in html_out
    assert "<svg" in html_out
    # The model name is exposed via an SVG <title> for hover/accessibility.
    assert "<title>" in html_out
    assert "m1" in html_out
    # Offline only: no external script or CDN fetches.
    assert not re.search(r"<script", html_out)
    assert not re.search(r'(href|src)\s*=\s*["\']https?://', html_out)


def test_render_charts_section_notes_omitted_points() -> None:
    models = _models(
        [
            _endpoint_record("m1", "t0", True, 0.01, prefill=200.0),
            _endpoint_record("nospeed", "t0", True, 0.01, prefill=None),
        ]
    )

    html_out = render_charts_section(models, ())

    assert "chart-note" in html_out
    assert "nospeed" in html_out


def test_render_charts_section_empty_when_no_data() -> None:
    assert render_charts_section((), ()) == ""


def _aggregate(model, *, attempts, pass_rate, mean_cost, prefill) -> EndpointModelAggregate:
    return EndpointModelAggregate(
        model=model,
        suite=None,
        run_mode="endpoint",
        attempts=attempts,
        passed=round(pass_rate * attempts),
        pass_rate=pass_rate,
        failure_count=0,
        infra_failures=0,
        model_failures=0,
        median_latency_seconds=1.0,
        median_ttft_seconds=0.1,
        median_prefill_tokens_per_second=prefill,
        median_decode_tokens_per_second=50.0,
        total_prompt_tokens=0,
        total_completion_tokens=0,
        total_cost_usd=mean_cost * attempts,
        mean_cost_usd=mean_cost,
        tasks=(),
    )


def test_cost_quality_omits_aggregate_without_attempts() -> None:
    empty = _aggregate("ghost", attempts=0, pass_rate=0.0, mean_cost=0.0, prefill=None)

    points, omitted = cost_quality_points([empty])

    assert points == []
    assert len(omitted) == 1
    assert "ghost" in omitted[0].label


def test_render_charts_section_scales_distinct_points() -> None:
    models = [
        _aggregate("cheap", attempts=2, pass_rate=0.5, mean_cost=0.001, prefill=80.0),
        _aggregate("pricey", attempts=2, pass_rate=1.0, mean_cost=0.05, prefill=240.0),
    ]

    html_out = render_charts_section(models, ())

    # Both distinct points are plotted (covers the non-degenerate bounds/scale path).
    assert html_out.count("<circle") >= 4  # 2 models × cost + speed charts
    assert "cheap" in html_out
    assert "pricey" in html_out


def test_render_charts_section_handles_single_flat_sweep_series() -> None:
    # A single sweep point (no polyline) at a constant throughput exercises the
    # degenerate-bounds branches without crashing.
    points = (
        SweepPoint(
            model="m1", context_tokens=4000, ttft_seconds=1.0, prefill_tokens_per_second=100.0
        ),
    )

    html_out = render_charts_section((), points)

    assert "<svg" in html_out
    assert "<polyline" not in html_out  # single point → no connecting line
    assert "m1" in html_out


def test_render_charts_section_draws_polyline_for_multi_point_series() -> None:
    # A model with two distinct context sizes draws a connecting polyline.
    points = (
        SweepPoint(
            model="m1", context_tokens=1000, ttft_seconds=1.0, prefill_tokens_per_second=180.0
        ),
        SweepPoint(
            model="m1", context_tokens=4000, ttft_seconds=1.0, prefill_tokens_per_second=120.0
        ),
    )

    html_out = render_charts_section((), points)

    assert "<polyline" in html_out  # >1 point per model → connecting line drawn


def test_render_charts_section_handles_all_zero_sweep_throughput() -> None:
    # When every plotted prefill value is zero, y_hi == y_lo and the renderer must
    # widen the range instead of dividing by zero.
    points = (
        SweepPoint(
            model="m1", context_tokens=1000, ttft_seconds=1.0, prefill_tokens_per_second=0.0
        ),
        SweepPoint(
            model="m1", context_tokens=4000, ttft_seconds=1.0, prefill_tokens_per_second=0.0
        ),
    )

    html_out = render_charts_section((), points)

    assert "<svg" in html_out
    assert "<polyline" in html_out


def test_scale_returns_midpoint_for_degenerate_range() -> None:
    # hi == lo cannot blow up the linear map; the midpoint is returned instead.
    assert _scale(5.0, 5.0, 5.0, 0.0, 100.0) == 50.0
