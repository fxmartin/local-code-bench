"""Story 16.1-001 — design-token layer and base styles.

Contract under test:

* ``local_code_bench.theme`` is the single home of every color literal — the
  token block (``TOKENS_CSS``) defines semantic CSS custom properties, and the
  base styles (``BASE_CSS``) plus every dashboard page resolve colors only
  through ``var(...)`` references.
* The palette is exactly: white/black anchors, one grey ramp (5–7 steps), and
  a single accent.
* Grep-enforceable AC: no hex/rgb/hsl color literal appears in any dashboard
  source module outside ``theme.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from local_code_bench import (
    dashboard,
    dashboard_charts,
    dashboard_server,
    theme,
    unified_dashboard,
)
from local_code_bench.inferencers import dashboard as inferencers_dashboard
from local_code_bench.results import append_jsonl

# ``(?<!&)`` skips numeric HTML entities (&#8593;); the trailing lookahead skips
# CSS id selectors / longer identifiers that merely start with hex-ish letters.
_HEX_RE = re.compile(r"(?<!&)#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?![0-9a-zA-Z_-])")
_COLOR_FN_RE = re.compile(r"\b(?:rgb|rgba|hsl|hsla)\(")

_SEMANTIC_TOKENS = (
    "--bg:",
    "--surface:",
    "--surface-hover:",
    "--border:",
    "--border-strong:",
    "--text:",
    "--text-muted:",
    "--accent:",
)
_SCALE_TOKENS = (
    "--font-sans:",
    "--font-mono:",
    "--text-xs:",
    "--text-sm:",
    "--text-base:",
    "--text-lg:",
    "--text-xl:",
    "--space-1:",
    "--space-2:",
    "--space-3:",
    "--space-4:",
    "--space-5:",
    "--space-6:",
    "--space-7:",
    "--radius-sm:",
    "--radius-md:",
    "--elevation-1:",
)

# The page templates named by the story, plus the chart module whose SVG palette
# must also resolve from the token layer.
_DASHBOARD_MODULES = (
    dashboard,
    dashboard_server,
    unified_dashboard,
    inferencers_dashboard,
    dashboard_charts,
)


def _hexes(text: str) -> list[str]:
    return [match.group(0).lower() for match in _HEX_RE.finditer(text)]


def _is_grey(hex_color: str) -> bool:
    digits = hex_color.lstrip("#")
    if len(digits) in (3, 4):
        digits = "".join(ch * 2 for ch in digits[:3])
    red, green, blue = digits[0:2], digits[2:4], digits[4:6]
    return red == green == blue


# --------------------------------------------------------------------------- #
# Token block shape
# --------------------------------------------------------------------------- #


def test_semantic_color_tokens_defined_in_token_block() -> None:
    for token in _SEMANTIC_TOKENS:
        assert token in theme.TOKENS_CSS, f"missing semantic token {token}"


def test_type_spacing_radius_elevation_scales_defined() -> None:
    for token in _SCALE_TOKENS:
        assert token in theme.TOKENS_CSS, f"missing scale token {token}"


def test_palette_is_anchors_grey_ramp_accent_and_danger() -> None:
    palette = _hexes(theme.TOKENS_CSS)
    assert theme.WHITE in palette
    assert theme.BLACK in palette
    greys = [color for color in palette if _is_grey(color)]
    accents = {color for color in palette if not _is_grey(color)}
    # One accent hue and one danger hue, each with two lightness stops
    # (stories 16.1-002 / 16.2-001 dual-value --accent and --danger so they
    # hold WCAG AA contrast against both the white and black anchors).
    assert accents == {theme.ACCENT, theme.ACCENT_DARK, theme.DANGER, theme.DANGER_DARK}
    # Grey ramp of 5–7 steps between the white/black anchors.
    ramp_steps = len(set(greys)) - 2
    assert 5 <= ramp_steps <= 7, f"grey ramp has {ramp_steps} steps"


def test_base_css_resolves_colors_only_through_tokens() -> None:
    assert _hexes(theme.BASE_CSS) == []
    assert not _COLOR_FN_RE.search(theme.BASE_CSS)
    assert "var(--" in theme.BASE_CSS


def test_theme_css_bundles_tokens_and_base_styles() -> None:
    assert theme.TOKENS_CSS in theme.THEME_CSS
    assert theme.BASE_CSS in theme.THEME_CSS


# --------------------------------------------------------------------------- #
# Print stylesheet (story 17.3-001) — light tokens forced, chrome hidden
# --------------------------------------------------------------------------- #


def test_print_css_forces_light_token_values() -> None:
    assert "@media print" in theme.PRINT_CSS
    # Same light-dark() tokens resolved light — not a second theme. The dark
    # override selector is repeated so a stored preference cannot out-cascade
    # the print media query.
    assert ':root, :root[data-theme="dark"] { color-scheme: light; }' in theme.PRINT_CSS


def test_print_css_hides_shared_chrome() -> None:
    hidden = (
        "nav, button, input, select, textarea, #theme-toggle, #modal,\n"
        "  .print-hide { display: none !important; }"
    )
    assert hidden in theme.PRINT_CSS


def test_print_css_is_token_pure_and_bundled_last() -> None:
    assert _hexes(theme.PRINT_CSS) == []
    assert not _COLOR_FN_RE.search(theme.PRINT_CSS)
    # Bundled after the mode overrides so the forced light scheme wins at
    # equal specificity against :root[data-theme="dark"].
    assert theme.THEME_CSS.endswith(theme.PRINT_CSS)


# --------------------------------------------------------------------------- #
# Grep-enforceable AC — no color literals outside the token module
# --------------------------------------------------------------------------- #


def test_no_color_literals_outside_theme_module() -> None:
    for module in _DASHBOARD_MODULES:
        source = Path(module.__file__).read_text(encoding="utf-8")
        stray_hexes = _hexes(source)
        assert stray_hexes == [], f"{module.__name__} contains color literals: {stray_hexes}"
        color_fns = _COLOR_FN_RE.findall(source)
        assert color_fns == [], f"{module.__name__} uses color functions: {color_fns}"


def test_chart_palette_is_sourced_from_theme() -> None:
    # Since story 16.2-002 the palette entries are var(--chart-*) references,
    # so the SVG re-colors live when the mode toggles.
    assert dashboard_charts._PALETTE == theme.CHART_SERIES
    assert dashboard_charts._POINT_COLOR == theme.CHART_SERIES[0]
    assert all(paint.startswith("var(--chart-") for paint in theme.CHART_SERIES)


# --------------------------------------------------------------------------- #
# Rendered surfaces consume the shared token block
# --------------------------------------------------------------------------- #


def _assert_page_uses_tokens(page: str, *, allowed: set[str]) -> None:
    assert "--bg:" in page
    assert "--accent:" in page
    assert theme.BASE_CSS.strip() in page
    stray = [color for color in _hexes(page) if color not in allowed]
    assert stray == [], f"page hex literals outside the token block: {stray}"


def _palette_hexes() -> set[str]:
    return set(_hexes(theme.TOKENS_CSS))


def test_live_results_page_uses_token_block() -> None:
    _assert_page_uses_tokens(dashboard_server.render_page(), allowed=_palette_hexes())


def test_unified_page_uses_token_block() -> None:
    _assert_page_uses_tokens(unified_dashboard.render_page(), allowed=_palette_hexes())


def test_inferencer_panel_uses_token_block() -> None:
    _assert_page_uses_tokens(inferencers_dashboard.render_page(), allowed=_palette_hexes())


def test_static_results_page_uses_token_block(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    engine = {"name": "ollama", "versions": {"ollama": "0.32.0"}, "capture_method": "live-api"}
    append_jsonl(
        path,
        {
            "run_mode": "endpoint",
            "model": "m1",
            "suite": "humaneval",
            "task_id": "HumanEval/0",
            "passed": True,
            "engine": engine,
            "cost_usd": 0.01,
            "metrics": {
                "latency_seconds": 1.0,
                "ttft_seconds": 0.2,
                "prefill_tokens_per_second": 200.0,
                "decode_tokens_per_second": 50.0,
            },
        },
    )
    append_jsonl(
        path,
        {
            "run_mode": "sweep",
            "model": "m1",
            "context_tokens": 2000,
            "engine": engine,
            "metrics": {"ttft_seconds": 1.5, "prefill_tokens_per_second": 180.0},
        },
    )

    content = dashboard.generate_dashboard([path], tmp_path / "dashboard.html")

    # Inline SVG series colors are var(--chart-*) references since 16.2-002,
    # so the token block's hexes are the only literals on the page.
    _assert_page_uses_tokens(content, allowed=_palette_hexes())
