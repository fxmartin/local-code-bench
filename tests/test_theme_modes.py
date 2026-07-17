"""Story 16.1-002 — designed light and dark modes with persistent toggle.

Contract under test:

* With no stored preference the page follows ``prefers-color-scheme`` (the
  ``:root`` token block declares ``color-scheme: light dark`` and every
  semantic color token is dual-valued via ``light-dark()``, with designed dark
  greys — not inversions or browser defaults).
* A ``[data-theme="light"|"dark"]`` attribute on the root element forces one
  scheme by overriding ``color-scheme``, so native controls and scrollbars
  follow the forced mode too.
* A tiny inline pre-paint script in ``<head>`` applies the preference stored
  in ``localStorage`` before first paint; the header toggle switches the
  attribute instantly and persists the choice under the same key.
* Text, controls, focus indicators, and status glyphs meet WCAG AA contrast
  against their backgrounds in both modes (4.5:1 for text, 3:1 for non-text).
* Any theme transition is guarded by ``prefers-reduced-motion``.
"""

from __future__ import annotations

import re

import pytest

from local_code_bench import dashboard, dashboard_server, theme, unified_dashboard
from local_code_bench.inferencers import dashboard as inferencers_dashboard

# --------------------------------------------------------------------------- #
# Token resolution — evaluate the CSS custom properties for one color scheme
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r"(--[\w-]+):\s*([^;]+);")
_LIGHT_DARK_RE = re.compile(r"light-dark\(\s*(.+?)\s*,\s*(.+?)\s*\)")
_VAR_RE = re.compile(r"var\((--[\w-]+)\)")
_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")


def _tokens() -> dict[str, str]:
    return {name: value.strip() for name, value in _TOKEN_RE.findall(theme.TOKENS_CSS)}


def _resolve(name: str, mode: str) -> str:
    """Resolve a semantic token to its hex literal for ``mode`` (light|dark)."""
    tokens = _tokens()
    value = tokens[name]
    for _ in range(10):
        light_dark = _LIGHT_DARK_RE.fullmatch(value)
        if light_dark:
            value = light_dark.group(1 if mode == "light" else 2)
            continue
        var_ref = _VAR_RE.fullmatch(value)
        if var_ref:
            value = tokens[var_ref.group(1)]
            continue
        if _HEX_RE.fullmatch(value):
            return value.lower()
        raise AssertionError(f"cannot resolve {name} ({mode}): stuck at {value!r}")
    raise AssertionError(f"cyclic token reference resolving {name}")


def _relative_luminance(hex_color: str) -> float:
    digits = hex_color.lstrip("#")
    channels = [int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    red, green, blue = linear
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


# --------------------------------------------------------------------------- #
# Dual-valued tokens follow the OS by default; [data-theme] forces one scheme
# --------------------------------------------------------------------------- #


def test_root_declares_both_color_schemes() -> None:
    assert "color-scheme: light dark;" in theme.TOKENS_CSS


def test_data_theme_attribute_forces_color_scheme() -> None:
    # Forcing color-scheme on the root flips every light-dark() token at once
    # and drags native controls/scrollbars along with it.
    assert re.search(r':root\[data-theme="light"\]\s*\{\s*color-scheme:\s*light;', theme.MODES_CSS)
    assert re.search(r':root\[data-theme="dark"\]\s*\{\s*color-scheme:\s*dark;', theme.MODES_CSS)


def test_semantic_color_tokens_are_dual_valued() -> None:
    tokens = _tokens()
    for name in (
        "--bg",
        "--surface",
        "--surface-hover",
        "--border",
        "--border-strong",
        "--text",
        "--text-muted",
        "--accent",
    ):
        assert "light-dark(" in tokens[name], f"{name} is not dual-valued: {tokens[name]}"


def test_dark_mode_uses_designed_greys_not_inversions() -> None:
    # Near-black canvas and off-white text — not #000/#fff inversions.
    assert _resolve("--bg", "dark") == theme.BLACK != "#000000"
    assert _resolve("--text", "dark") == theme.GREY_RAMP[0] != "#ffffff"
    # The dark surfaces sit on the designed grey ramp, not on inverted light greys.
    assert _resolve("--surface-hover", "dark") == theme.GREY_RAMP[6]
    assert _resolve("--border", "dark") == theme.GREY_RAMP[6]


def test_accent_is_tuned_per_scheme() -> None:
    assert _resolve("--accent", "light") == theme.ACCENT
    assert _resolve("--accent", "dark") == theme.ACCENT_DARK
    assert theme.ACCENT != theme.ACCENT_DARK


# --------------------------------------------------------------------------- #
# WCAG AA contrast in both modes (4.5:1 text, 3:1 non-text)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize(
    "token", ["--text", "--text-muted", "--accent", "--ok-fg", "--err-fg", "--warn-fg"]
)
def test_text_tokens_meet_aa_contrast(mode: str, token: str) -> None:
    ratio = _contrast(_resolve(token, mode), _resolve("--bg", mode))
    assert ratio >= 4.5, f"{token} on --bg is {ratio:.2f}:1 in {mode} mode"


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("token", ["--status-on", "--status-off", "--status-warn"])
def test_status_glyphs_meet_aa_non_text_contrast(mode: str, token: str) -> None:
    ratio = _contrast(_resolve(token, mode), _resolve("--bg", mode))
    assert ratio >= 3.0, f"{token} on --bg is {ratio:.2f}:1 in {mode} mode"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_focus_indicator_meets_aa_non_text_contrast(mode: str) -> None:
    # :focus-visible outlines resolve through --accent (see theme.BASE_CSS).
    assert "outline: 2px solid var(--accent)" in theme.BASE_CSS
    ratio = _contrast(_resolve("--accent", mode), _resolve("--bg", mode))
    assert ratio >= 3.0, f"focus outline on --bg is {ratio:.2f}:1 in {mode} mode"


def test_status_glyph_colors_are_distinct_within_each_mode() -> None:
    for mode in ("light", "dark"):
        resolved = {_resolve(t, mode) for t in ("--status-on", "--status-off", "--status-warn")}
        assert len(resolved) == 3, f"status glyphs collide in {mode} mode: {resolved}"


# --------------------------------------------------------------------------- #
# Pre-paint script, toggle behavior, reduced motion
# --------------------------------------------------------------------------- #


def test_pre_paint_script_applies_stored_preference() -> None:
    assert f'localStorage.getItem("{theme.THEME_STORAGE_KEY}")' in theme.THEME_INIT_JS
    assert "dataset.theme" in theme.THEME_INIT_JS
    # Storage failures (private mode, file://) must not break the page.
    assert "try" in theme.THEME_INIT_JS and "catch" in theme.THEME_INIT_JS


def test_toggle_persists_choice_and_follows_os_until_overridden() -> None:
    assert f'localStorage.setItem("{theme.THEME_STORAGE_KEY}"' in theme.THEME_TOGGLE_JS
    assert '"(prefers-color-scheme: dark)"' in theme.THEME_TOGGLE_JS
    assert '"click"' in theme.THEME_TOGGLE_JS
    # Keeps tracking OS scheme changes while no preference is stored.
    assert '"change"' in theme.THEME_TOGGLE_JS


def test_theme_transitions_are_guarded_by_reduced_motion() -> None:
    for css in (theme.TOKENS_CSS, theme.BASE_CSS):
        assert "transition" not in css
    if "transition" in theme.MODES_CSS:
        guard = theme.MODES_CSS.index("@media (prefers-reduced-motion: no-preference)")
        assert guard < theme.MODES_CSS.index("transition")


def test_theme_css_bundles_mode_overrides() -> None:
    assert theme.MODES_CSS in theme.THEME_CSS


# --------------------------------------------------------------------------- #
# Every dashboard surface ships the pre-paint script and the header toggle
# --------------------------------------------------------------------------- #


def _rendered_pages(tmp_path) -> dict[str, str]:
    return {
        "live-results": dashboard_server.render_page(),
        "unified": unified_dashboard.render_page(),
        "inferencers": inferencers_dashboard.render_page(),
        "static": dashboard.generate_dashboard([], tmp_path / "dashboard.html"),
    }


def test_every_surface_applies_stored_mode_before_first_paint(tmp_path) -> None:
    for name, page in _rendered_pages(tmp_path).items():
        position = page.find(theme.THEME_INIT_JS.strip())
        assert position != -1, f"{name}: pre-paint script missing"
        assert position < page.index("</head>"), f"{name}: pre-paint script not in <head>"


def test_every_surface_has_the_mode_toggle_chrome(tmp_path) -> None:
    for name, page in _rendered_pages(tmp_path).items():
        assert 'id="theme-toggle"' in page, f"{name}: toggle button missing"
        assert theme.THEME_TOGGLE_JS.strip() in page, f"{name}: toggle script missing"
        assert theme.MODES_CSS in page, f"{name}: [data-theme] override CSS missing"
