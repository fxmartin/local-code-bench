"""SVG tradeoff and sweep charts for the static dashboard (story 07.4-002).

Renders three basic charts straight from the dashboard aggregates so FX can see
tradeoffs faster than by scanning tables:

* **Cost vs Quality** — mean cost per task against pass@1, per endpoint model.
* **Quality vs Speed** — median prefill throughput against pass@1, per model.
* **Sweep — Prefill Throughput by Context Size** — one line per model.

Charts are emitted as inline SVG generated in Python from the embedded data. That
keeps the static artifact fully offline: no matplotlib PNGs, no JavaScript, and no
CDN fetches. A point is plotted only when its required metrics are present; models
or sweep observations with incomplete metrics are dropped and surfaced in a visible
data-quality note rather than plotted as misleading zeros.

The charts speak the theme's monochrome-plus-accent language (story 16.2-002):
every paint is a ``var(--chart-*)`` reference into the shared token layer, so
axes, gridlines, labels, and series re-color live when the mode toggles. The
accent is reserved for the highlighted first series; supporting series combine
grey ramp stops with per-series dash patterns and marker shapes, because hue
alone can no longer tell them apart.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass

from local_code_bench.dashboard_model import EndpointModelAggregate, SweepPoint
from local_code_bench.theme import CHART_SERIES

# Series paints come from the shared token layer; the inline SVG resolves the
# custom properties against the embedding page's stylesheet at paint time.
_PALETTE = CHART_SERIES
_POINT_COLOR = _PALETTE[0]

# Per-series line/marker variation. Supporting series cycle the grey stops with
# staggered dash patterns and marker shapes so the (paint, dash, marker) combo
# stays unique well past eight series.
_DASHES = ("", "7 4", "2 3", "10 4 2 4")
_MARKERS = ("circle", "square", "diamond", "triangle")
_LEGEND_GLYPHS = {"circle": "●", "square": "■", "diamond": "◆", "triangle": "▲"}


@dataclass(frozen=True)
class SeriesStyle:
    """Paint plus line/marker treatment for one sweep series."""

    paint: str
    dash: str
    marker: str


def series_style(index: int) -> SeriesStyle:
    """Style for sweep series ``index`` — accent first, grey ramp variants after."""

    if index == 0:
        return SeriesStyle(_PALETTE[0], "", _MARKERS[0])
    support = index - 1
    greys = _PALETTE[1:]
    return SeriesStyle(
        greys[support % len(greys)],
        _DASHES[support % len(_DASHES)],
        _MARKERS[support % len(_MARKERS)],
    )


# SVG geometry (viewBox units). Plot area is the canvas minus margins.
_W = 480
_H = 300
_LEFT = 62
_RIGHT = 16
_TOP = 18
_BOTTOM = 46
_PX_LEFT = _LEFT
_PX_RIGHT = _W - _RIGHT
_PX_TOP = _TOP
_PX_BOTTOM = _H - _BOTTOM


@dataclass(frozen=True)
class ChartPoint:
    """A single plottable observation."""

    label: str
    x: float
    y: float


@dataclass(frozen=True)
class OmittedPoint:
    """An observation dropped from a chart because metrics were incomplete."""

    label: str
    reason: str


# --------------------------------------------------------------------------- #
# Point selection — the "omit incomplete metrics" rules live here, pure and
# unit-tested independently of any SVG rendering.
# --------------------------------------------------------------------------- #


def cost_quality_points(
    models: Sequence[EndpointModelAggregate],
) -> tuple[list[ChartPoint], list[OmittedPoint]]:
    """Map endpoint models to (mean cost per task, pass@1) points."""

    points: list[ChartPoint] = []
    omitted: list[OmittedPoint] = []
    for model in models:
        label = _label(model.model, model.suite, model.engine_label)
        if model.attempts <= 0:
            omitted.append(OmittedPoint(label, "no attempts recorded"))
            continue
        points.append(ChartPoint(label, model.mean_cost_usd, model.pass_rate))
    return points, omitted


def quality_speed_points(
    models: Sequence[EndpointModelAggregate],
) -> tuple[list[ChartPoint], list[OmittedPoint]]:
    """Map endpoint models to (median prefill throughput, pass@1) points."""

    points: list[ChartPoint] = []
    omitted: list[OmittedPoint] = []
    for model in models:
        label = _label(model.model, model.suite, model.engine_label)
        speed = model.median_prefill_tokens_per_second
        if model.attempts <= 0 or speed is None:
            omitted.append(OmittedPoint(label, "missing prefill throughput"))
            continue
        points.append(ChartPoint(label, speed, model.pass_rate))
    return points, omitted


def sweep_series(
    points: Sequence[SweepPoint],
) -> tuple[dict[str, list[ChartPoint]], list[OmittedPoint]]:
    """Group sweep observations into per-model (context, prefill) series."""

    series: dict[str, list[ChartPoint]] = {}
    omitted: list[OmittedPoint] = []
    for point in points:
        series_label = _label(point.model, None, point.engine_label)
        label = f"{series_label} @ {point.context_tokens:,}"
        if point.prefill_tokens_per_second is None:
            omitted.append(OmittedPoint(label, "missing prefill throughput"))
            continue
        series.setdefault(series_label, []).append(
            ChartPoint(label, float(point.context_tokens), point.prefill_tokens_per_second)
        )
    for plotted in series.values():
        plotted.sort(key=lambda point: point.x)
    return series, omitted


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_charts_section(
    models: Sequence[EndpointModelAggregate],
    sweep_points: Sequence[SweepPoint],
) -> str:
    """Render the tradeoff and sweep charts as HTML sections (empty if no data)."""

    if not models and not sweep_points:
        return ""

    cost_points, cost_omitted = cost_quality_points(models)
    speed_points, speed_omitted = quality_speed_points(models)
    series, sweep_omitted = sweep_series(sweep_points)

    return "\n".join(
        [
            _scatter_section(
                "Cost vs Quality",
                "Mean cost per task (USD)",
                "pass@1",
                cost_points,
                cost_omitted,
                _fmt_cost,
                _fmt_pct,
                y_bounds=(0.0, 1.0),
            ),
            _scatter_section(
                "Quality vs Speed",
                "Median prefill (tok/s)",
                "pass@1",
                speed_points,
                speed_omitted,
                _fmt_speed,
                _fmt_pct,
                y_bounds=(0.0, 1.0),
            ),
            _sweep_section(
                "Sweep — Prefill Throughput by Context Size",
                "Context tokens",
                "Prefill (tok/s)",
                series,
                sweep_omitted,
            ),
        ]
    )


def _scatter_section(
    title: str,
    x_title: str,
    y_title: str,
    points: list[ChartPoint],
    omitted: list[OmittedPoint],
    x_fmt,
    y_fmt,
    *,
    y_bounds: tuple[float, float] | None = None,
) -> str:
    if not points:
        return _chart_section(title, '<p class="empty">Not enough data to chart.</p>', omitted)

    x_lo, x_hi = _bounds([point.x for point in points])
    y_lo, y_hi = y_bounds if y_bounds is not None else _bounds([point.y for point in points])

    parts = _axes_svg(x_title, y_title, x_lo, x_hi, y_lo, y_hi, x_fmt, y_fmt)
    for point in points:
        cx = _scale(point.x, x_lo, x_hi, _PX_LEFT, _PX_RIGHT)
        cy = _scale(point.y, y_lo, y_hi, _PX_BOTTOM, _PX_TOP)
        tooltip = f"{point.label}: {x_fmt(point.x)}, {y_fmt(point.y)}"
        parts.append(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="5" style="fill:{_POINT_COLOR}">'
            f"<title>{html.escape(tooltip)}</title></circle>"
        )
    return _chart_section(title, _svg(title, parts), omitted)


def _sweep_section(
    title: str,
    x_title: str,
    y_title: str,
    series: dict[str, list[ChartPoint]],
    omitted: list[OmittedPoint],
) -> str:
    all_points = [point for plotted in series.values() for point in plotted]
    if not all_points:
        return _chart_section(title, '<p class="empty">No sweep data to chart.</p>', omitted)

    x_lo, x_hi = _bounds([point.x for point in all_points])
    y_lo, y_hi = 0.0, max(point.y for point in all_points)
    if y_hi == y_lo:
        y_hi = y_lo + 1.0

    parts = _axes_svg(x_title, y_title, x_lo, x_hi, y_lo, y_hi, _fmt_int, _fmt_speed)
    legend: list[str] = []
    for index, model in enumerate(sorted(series)):
        style = series_style(index)
        plotted = series[model]
        coords = [
            (
                _scale(point.x, x_lo, x_hi, _PX_LEFT, _PX_RIGHT),
                _scale(point.y, y_lo, y_hi, _PX_BOTTOM, _PX_TOP),
            )
            for point in plotted
        ]
        if len(coords) > 1:
            line = " ".join(f"{cx:.2f},{cy:.2f}" for cx, cy in coords)
            dash = f' stroke-dasharray="{style.dash}"' if style.dash else ""
            parts.append(
                f'<polyline points="{line}" fill="none" style="stroke:{style.paint}" '
                f'stroke-width="2"{dash}/>'
            )
        for (cx, cy), point in zip(coords, plotted):
            tooltip = f"{point.label}: {_fmt_speed(point.y)}"
            parts.append(_marker_svg(style, cx, cy, tooltip))
        glyph = _LEGEND_GLYPHS[style.marker]
        legend.append(
            f'<li><span class="swatch" style="color:{style.paint}">{glyph}</span>'
            f"{html.escape(model)}</li>"
        )
    body = _svg(title, parts) + f'<ul class="legend">{"".join(legend)}</ul>'
    return _chart_section(title, body, omitted)


def _marker_svg(style: SeriesStyle, cx: float, cy: float, tooltip: str) -> str:
    """One data-point marker in the series' shape, painted from the token layer."""

    title = f"<title>{html.escape(tooltip)}</title>"
    fill = f'style="fill:{style.paint}"'
    if style.marker == "square":
        return (
            f'<rect x="{cx - 3.5:.2f}" y="{cy - 3.5:.2f}" width="7" height="7" '
            f"{fill}>{title}</rect>"
        )
    if style.marker == "diamond":
        return (
            f'<rect x="{cx - 3.5:.2f}" y="{cy - 3.5:.2f}" width="7" height="7" '
            f'transform="rotate(45 {cx:.2f} {cy:.2f})" {fill}>{title}</rect>'
        )
    if style.marker == "triangle":
        return (
            f'<path d="M {cx:.2f} {cy - 4.5:.2f} L {cx + 4.2:.2f} {cy + 3.5:.2f} '
            f'L {cx - 4.2:.2f} {cy + 3.5:.2f} Z" {fill}>{title}</path>'
        )
    return f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="4" {fill}>{title}</circle>'


def _axes_svg(
    x_title: str,
    y_title: str,
    x_lo: float,
    x_hi: float,
    y_lo: float,
    y_hi: float,
    x_fmt,
    y_fmt,
) -> list[str]:
    mid_y = (_PX_TOP + _PX_BOTTOM) / 2
    parts = [
        # Hairline gridlines at the top (y max) and midpoint of the plot area.
        f'<line x1="{_PX_LEFT}" y1="{_PX_TOP}" x2="{_PX_RIGHT}" y2="{_PX_TOP}" class="grid"/>',
        f'<line x1="{_PX_LEFT}" y1="{mid_y:.0f}" x2="{_PX_RIGHT}" y2="{mid_y:.0f}" class="grid"/>',
        f'<line x1="{_PX_LEFT}" y1="{_PX_BOTTOM}" x2="{_PX_RIGHT}" y2="{_PX_BOTTOM}" '
        'class="axis"/>',
        f'<line x1="{_PX_LEFT}" y1="{_PX_TOP}" x2="{_PX_LEFT}" y2="{_PX_BOTTOM}" class="axis"/>',
        # X tick labels (min / max).
        f'<text x="{_PX_LEFT}" y="{_PX_BOTTOM + 14}" class="tick">{html.escape(x_fmt(x_lo))}</text>',
        f'<text x="{_PX_RIGHT}" y="{_PX_BOTTOM + 14}" class="tick" text-anchor="end">'
        f"{html.escape(x_fmt(x_hi))}</text>",
        # Y tick labels (min / max).
        f'<text x="{_PX_LEFT - 6}" y="{_PX_BOTTOM}" class="tick" text-anchor="end">'
        f"{html.escape(y_fmt(y_lo))}</text>",
        f'<text x="{_PX_LEFT - 6}" y="{_PX_TOP + 8}" class="tick" text-anchor="end">'
        f"{html.escape(y_fmt(y_hi))}</text>",
        # Axis titles.
        f'<text x="{(_PX_LEFT + _PX_RIGHT) / 2:.0f}" y="{_H - 8}" class="axis-title" '
        f'text-anchor="middle">{html.escape(x_title)}</text>',
        f'<text x="14" y="{(_PX_TOP + _PX_BOTTOM) / 2:.0f}" class="axis-title" '
        f'text-anchor="middle" transform="rotate(-90 14 {(_PX_TOP + _PX_BOTTOM) / 2:.0f})">'
        f"{html.escape(y_title)}</text>",
    ]
    return parts


def _svg(title: str, parts: list[str]) -> str:
    return (
        f'<svg viewBox="0 0 {_W} {_H}" class="chart-svg" role="img" '
        f'aria-label="{html.escape(title)}">{"".join(parts)}</svg>'
    )


def _chart_section(title: str, body: str, omitted: list[OmittedPoint]) -> str:
    return (
        f'<section class="chart"><h2>{html.escape(title)}</h2>{body}'
        f"{_omitted_note(omitted)}</section>"
    )


def _omitted_note(omitted: list[OmittedPoint]) -> str:
    if not omitted:
        return ""
    items = "; ".join(
        f"{html.escape(point.label)} ({html.escape(point.reason)})" for point in omitted
    )
    return f'<p class="chart-note">Omitted — incomplete metrics: {items}</p>'


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _label(model: str, suite: str | None, engine: str) -> str:
    label = f"{model} · {suite}" if suite else model
    return f"{label} · {engine}" if engine != "unknown (legacy)" else label


def _bounds(values: list[float]) -> tuple[float, float]:
    lo = min(values)
    hi = max(values)
    if lo == hi:
        pad = abs(lo) * 0.1 or 1.0
        return lo - pad, hi + pad
    return lo, hi


def _scale(value: float, lo: float, hi: float, lo_px: float, hi_px: float) -> float:
    if hi == lo:
        return (lo_px + hi_px) / 2
    return lo_px + (value - lo) / (hi - lo) * (hi_px - lo_px)


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _fmt_cost(value: float) -> str:
    return f"${value:.4f}"


def _fmt_speed(value: float) -> str:
    return f"{value:.0f} tok/s"


def _fmt_int(value: float) -> str:
    return f"{int(round(value)):,}"
