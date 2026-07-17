"""Story 16.2-002 — charts in the monochrome-plus-accent language.

Contract under test:

* Axes, gridlines, labels, and series colors all resolve from the token layer:
  the token block defines a chart group (``--chart-axis``, ``--chart-grid``,
  ``--chart-label``, ``--chart-accent``, ``--chart-grey-*``) and the rendered
  SVG carries only ``var(...)`` paints — no baked-in hex — so a mode toggle
  re-colors the charts live, without a reload.
* Under the near-monochrome palette, sweep series are distinguishable via the
  grey ramp plus per-series dash patterns and marker shapes; the accent is
  reserved for the highlighted first series.
* Chart text and marks meet the same WCAG AA bar as the rest of the UI in both
  modes (4.5:1 for labels, 3:1 for data marks).
"""

from __future__ import annotations

import re

import pytest

from local_code_bench import dashboard, dashboard_charts, theme
from local_code_bench.dashboard_model import SweepPoint

# Reuse the token-resolution helpers proven in the 16.1-002 mode tests
# (tests/ is on sys.path under pytest's prepend import mode).
from test_dashboard_charts import _aggregate
from test_theme_modes import _contrast, _resolve, _tokens

_HEX_RE = re.compile(r"(?<!&)#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?![0-9a-zA-Z_-])")
_COLOR_FN_RE = re.compile(r"\b(?:rgb|rgba|hsl|hsla)\(")

_CHART_GREY_TOKENS = ("--chart-grey-1", "--chart-grey-2", "--chart-grey-3")


def _sweep_points(n_models: int, contexts=(1000, 4000)) -> tuple[SweepPoint, ...]:
    return tuple(
        SweepPoint(
            model=f"m{i}",
            context_tokens=context,
            ttft_seconds=1.0,
            prefill_tokens_per_second=100.0 + 10 * i + context / 100,
        )
        for i in range(n_models)
        for context in contexts
    )


# --------------------------------------------------------------------------- #
# Token layer — chart group defined, mode-aware, AA-compliant
# --------------------------------------------------------------------------- #


def test_chart_tokens_defined_in_token_block() -> None:
    tokens = _tokens()
    for name in ("--chart-axis", "--chart-grid", "--chart-label", "--chart-accent"):
        assert name in tokens, f"missing chart token {name}"
    for name in _CHART_GREY_TOKENS:
        assert name in tokens, f"missing chart series token {name}"


def test_chart_series_grey_tokens_are_mode_aware() -> None:
    tokens = _tokens()
    for name in _CHART_GREY_TOKENS:
        assert "light-dark(" in tokens[name], f"{name} is not dual-valued: {tokens[name]}"


def test_chart_palette_is_var_references_with_accent_reserved_first() -> None:
    # Every series paint is a token reference (hence live mode adaptation) and
    # the accent appears exactly once, as the highlighted first series.
    for paint in theme.CHART_SERIES:
        assert re.fullmatch(r"var\(--chart-[\w-]+\)", paint), f"not a chart token ref: {paint}"
    assert theme.CHART_SERIES[0] == "var(--chart-accent)"
    assert "var(--chart-accent)" not in theme.CHART_SERIES[1:]
    for paint in theme.CHART_SERIES[1:]:
        assert "grey" in paint, f"supporting series paint is not a grey: {paint}"


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("token", [*_CHART_GREY_TOKENS, "--chart-accent"])
def test_chart_marks_meet_aa_non_text_contrast(mode: str, token: str) -> None:
    ratio = _contrast(_resolve(token, mode), _resolve("--bg", mode))
    assert ratio >= 3.0, f"{token} on --bg is {ratio:.2f}:1 in {mode} mode"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_chart_labels_meet_aa_text_contrast(mode: str) -> None:
    ratio = _contrast(_resolve("--chart-label", mode), _resolve("--bg", mode))
    assert ratio >= 4.5, f"--chart-label on --bg is {ratio:.2f}:1 in {mode} mode"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_chart_series_greys_are_distinct_within_each_mode(mode: str) -> None:
    resolved = {_resolve(token, mode) for token in _CHART_GREY_TOKENS}
    assert len(resolved) == len(_CHART_GREY_TOKENS), f"series greys collide in {mode}: {resolved}"


# --------------------------------------------------------------------------- #
# Rendered SVG — token paints only, so a mode toggle re-colors without reload
# --------------------------------------------------------------------------- #


def test_rendered_charts_carry_no_baked_in_colors() -> None:
    html_out = dashboard_charts.render_charts_section((), _sweep_points(4))
    assert _HEX_RE.findall(html_out) == []
    assert not _COLOR_FN_RE.search(html_out)
    assert "var(--chart-" in html_out


def test_axes_gridlines_and_labels_resolve_from_chart_tokens() -> None:
    html_out = dashboard_charts.render_charts_section((), _sweep_points(1))
    assert 'class="axis"' in html_out
    assert 'class="grid"' in html_out
    assert 'class="tick"' in html_out
    # The static page maps those classes onto the chart tokens.
    assert "var(--chart-axis)" in dashboard._PAGE_CSS
    assert "var(--chart-grid)" in dashboard._PAGE_CSS
    assert "var(--chart-label)" in dashboard._PAGE_CSS


def test_accent_is_reserved_for_the_highlighted_sweep_series() -> None:
    html_out = dashboard_charts.render_charts_section((), _sweep_points(4))
    polylines = re.findall(r"<polyline[^>]*>", html_out)
    assert len(polylines) == 4
    accented = [line for line in polylines if "var(--chart-accent)" in line]
    assert len(accented) == 1, "accent must paint exactly one (highlighted) series line"
    for line in polylines:
        if line not in accented:
            assert "var(--chart-grey-" in line, f"supporting series is not grey: {line}"


def test_sweep_series_styles_are_pairwise_distinct() -> None:
    # Under a near-monochrome palette, (paint, dash, marker) must differ per
    # series so the grey ramp plus line style/markers keeps them tellable apart.
    styles = [dashboard_charts.series_style(index) for index in range(8)]
    combos = {(style.paint, style.dash, style.marker) for style in styles}
    assert len(combos) == 8, "series styles collide within the first eight series"


def test_supporting_series_use_dash_patterns_and_marker_shapes() -> None:
    html_out = dashboard_charts.render_charts_section((), _sweep_points(4))
    assert "stroke-dasharray" in html_out
    # At least one non-circle marker shape is drawn for the supporting series.
    assert re.search(r"<(rect|path)[^>]*var\(--chart-grey-", html_out)


def test_legend_swatches_echo_series_paint_and_marker_glyph() -> None:
    html_out = dashboard_charts.render_charts_section((), _sweep_points(4))
    legend = html_out[html_out.index('<ul class="legend">') :]
    assert "var(--chart-accent)" in legend
    assert "var(--chart-grey-" in legend
    glyphs = {glyph for glyph in ("●", "■", "◆", "▲") if glyph in legend}
    assert len(glyphs) >= 2, "legend glyphs do not echo the per-series markers"


def test_scatter_points_use_the_accent_token() -> None:
    models = [
        _aggregate("cheap", attempts=2, pass_rate=0.5, mean_cost=0.001, prefill=80.0),
        _aggregate("pricey", attempts=2, pass_rate=1.0, mean_cost=0.05, prefill=240.0),
    ]
    html_out = dashboard_charts.render_charts_section(models, ())
    assert "var(--chart-accent)" in html_out
    assert _HEX_RE.findall(html_out) == []
