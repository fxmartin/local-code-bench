"""Shared design-token layer and base styles for every dashboard surface (story 16.1-001).

This module is the **only** place in the codebase where color literals may
appear — a test (``tests/test_theme.py``) greps every dashboard module and
rendered page to enforce it. The layer has two parts:

* ``TOKENS_CSS`` — the token block: palette anchors (white/black), one grey
  ramp, a single accent, and the semantic custom properties (``--bg``,
  ``--surface``, ``--border``, ``--text``, ``--text-muted``, ``--accent``, …)
  every component rule resolves through, plus type/spacing/radius/elevation
  scales. Light and dark schemes are both derived from the same palette via
  ``light-dark()``, so no per-page dark-mode media queries are needed.
* ``BASE_CSS`` — token-driven styles for the shared primitives (page shell,
  headings, tables, buttons, inputs, badges/dots, status lines, modal card):
  system font stack, hairline borders, restrained radii, and no decorative
  gradients or drop shadows. Status semantics are locked (story 16.2-001):
  ``--danger`` is the only status hue and is reserved for failures and
  destructive actions; pass/ok/warn stay monochrome, distinguished by glyph +
  weight so no state relies on color alone. The accent carries focus, primary
  emphasis (``.act``), and live progress (``.progress``).
* ``MODES_CSS`` + the theme-toggle chrome (story 16.1-002) — a
  ``[data-theme="light"|"dark"]`` root attribute forces one ``color-scheme``
  (flipping every ``light-dark()`` token and native controls/scrollbars at
  once); ``THEME_HEAD_SNIPPET`` applies the ``localStorage`` preference before
  first paint and ``THEME_TOGGLE_SNIPPET`` is the persistent header toggle.

Pages embed ``THEME_CSS`` (tokens + modes + base) plus the two chrome snippets
and keep only layout-specific rules of their own, expressed exclusively as
``var(...)`` references. Restyling the
UI is therefore a token edit here, not a hunt through per-page hex literals.

Story 16.4-001 makes the accent/danger hues and the initial mode configurable:
a :class:`ThemeConfig` (fed from the ``theme:`` block of
``configs/settings.yaml`` via :func:`local_code_bench.settings.load_theme_config`)
parameterizes :func:`tokens_css` / :func:`theme_css` / :func:`theme_head_snippet`.
The dark-mode stop of each hue is *derived* (:func:`dark_tint` lifts lightness,
hue preserved, until WCAG AA against the dark canvas), so the one-hue-per-role
rule survives customization; the module-level constants stay as the shipped
defaults so this file remains the single home of color literals.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Palette — white/black anchors, one neutral grey ramp (7 steps), one accent.
# --------------------------------------------------------------------------- #

WHITE = "#ffffff"
BLACK = "#0a0a0a"

# Pure-neutral ramp (r == g == b), lightest to darkest.
GREY_RAMP = (
    "#f5f5f5",
    "#e5e5e5",
    "#d4d4d4",
    "#a3a3a3",
    "#737373",
    "#404040",
    "#262626",
)

# Shipped theme defaults (story 16.4-001): a dark blue accent, a dark red
# danger, and the OS scheme as the initial mode. These apply whenever the
# ``theme:`` block of configs/settings.yaml is absent, keeping the file additive.
DEFAULT_ACCENT = "#1e40af"
DEFAULT_DANGER = "#991b1b"
DEFAULT_MODE = "system"

#: Valid values for the configured initial mode.
THEME_MODES = ("light", "dark", "system")

#: WCAG AA minimum contrast for normal text.
_AA_TEXT_CONTRAST = 4.5


def _rgb(hex_color: str) -> tuple[float, float, float]:
    digits = hex_color.lstrip("#")
    red, green, blue = (int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return red, green, blue


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(channel * 255):02x}" for channel in rgb)


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of a ``#RRGGBB`` color."""

    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in _rgb(hex_color)
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio between two ``#RRGGBB`` colors (1:1 … 21:1)."""

    lighter, darker = sorted(
        (relative_luminance(first), relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def dark_tint(hex_color: str) -> str:
    """The dark-mode stop derived from a configured hue.

    Lifts lightness (hue and saturation preserved, so one hue per role survives
    customization) in small steps until the color meets AA 4.5:1 against the
    dark canvas (``BLACK``); a hue that already meets it is returned unchanged.
    """

    if contrast_ratio(hex_color, BLACK) >= _AA_TEXT_CONTRAST:
        return hex_color
    hue, lightness, saturation = colorsys.rgb_to_hls(*_rgb(hex_color))
    while lightness < 1.0:
        lightness = min(1.0, lightness + 0.01)
        candidate = _hex(colorsys.hls_to_rgb(hue, lightness, saturation))
        if contrast_ratio(candidate, BLACK) >= _AA_TEXT_CONTRAST:
            return candidate
    return WHITE  # pragma: no cover - lightness 1.0 is white, which always passes


def accent_contrast(stop: str) -> str:
    """The readable text anchor on an accent-filled surface, as a var reference."""

    white_wins = contrast_ratio(stop, WHITE) >= contrast_ratio(stop, BLACK)
    return "var(--white)" if white_wins else "var(--black)"


@dataclass(frozen=True)
class ThemeConfig:
    """The configurable theme surface: light-mode hues plus the initial mode.

    Dark-mode stops are always derived (:func:`dark_tint`), never configured,
    so a customized theme keeps the one-hue-per-role rule and AA contrast on
    the dark canvas by construction.
    """

    accent: str = DEFAULT_ACCENT
    danger: str = DEFAULT_DANGER
    default_mode: str = DEFAULT_MODE


def contrast_warnings(config: ThemeConfig | None = None) -> list[str]:
    """AA warnings for configured hues against both mode backgrounds.

    Pure luminance math, advisory only (story 16.4-001): the editor shows these
    next to a saved theme edit but never blocks the write — FX owns the call.
    """

    resolved = config if config is not None else ThemeConfig()
    warnings = []
    for role, hue in (("accent", resolved.accent), ("danger", resolved.danger)):
        for mode, stop, background in (
            ("light", hue, WHITE),
            ("dark", dark_tint(hue), BLACK),
        ):
            ratio = contrast_ratio(stop, background)
            if ratio < _AA_TEXT_CONTRAST:
                warnings.append(
                    f"theme {role} {stop} is {ratio:.2f}:1 against the {mode}-mode "
                    "background — below the WCAG AA 4.5:1 bar; saved anyway"
                )
    return warnings


# Accent, tuned per scheme (story 16.1-002): no single blue holds AA 4.5:1
# against both the white and the near-black anchor, so --accent is dual-valued —
# the configured stop for light mode, a derived lifted stop for dark mode.
ACCENT = DEFAULT_ACCENT
ACCENT_DARK = dark_tint(DEFAULT_ACCENT)

# Danger red (story 16.2-001): the one status hue, reserved for failures and
# destructive actions. Dual-valued like the accent so it holds AA contrast on
# both anchors; every other status state stays monochrome (glyph + weight).
DANGER = DEFAULT_DANGER
DANGER_DARK = dark_tint(DEFAULT_DANGER)

# Data-series paints for the inline SVG charts (story 16.2-002): the charts
# speak the same monochrome-plus-accent language as the rest of the UI. Every
# entry is a var(--chart-*) reference into the token block below, so the SVG
# re-colors live when the mode toggles — no repaint-on-reload. The accent leads
# and is reserved for the highlighted first series; supporting series cycle
# mode-aware grey ramp stops, and the chart module layers dash patterns and
# marker shapes on top for distinguishability that hue no longer provides.
CHART_ACCENT = "var(--chart-accent)"
CHART_GREYS = (
    "var(--chart-grey-1)",
    "var(--chart-grey-2)",
    "var(--chart-grey-3)",
)
CHART_SERIES = (CHART_ACCENT, *CHART_GREYS)


# --------------------------------------------------------------------------- #
# Token block — the sole home of color literals in any stylesheet. Since story
# 16.4-001 it is a function of the configured theme; the module-level constant
# keeps the shipped defaults for import-time consumers.
# --------------------------------------------------------------------------- #


def tokens_css(config: ThemeConfig | None = None) -> str:
    """The ``:root`` token block for a theme, defaults when none is given."""

    resolved = config if config is not None else ThemeConfig()
    accent, accent_dark = resolved.accent, dark_tint(resolved.accent)
    danger, danger_dark = resolved.danger, dark_tint(resolved.danger)
    return f"""\
:root {{
  color-scheme: light dark;

  /* Palette — anchors, grey ramp, single accent. */
  --white: {WHITE};
  --black: {BLACK};
  --grey-1: {GREY_RAMP[0]};
  --grey-2: {GREY_RAMP[1]};
  --grey-3: {GREY_RAMP[2]};
  --grey-4: {GREY_RAMP[3]};
  --grey-5: {GREY_RAMP[4]};
  --grey-6: {GREY_RAMP[5]};
  --grey-7: {GREY_RAMP[6]};

  /* Semantic colors — component rules resolve only through these. */
  --bg: light-dark(var(--white), var(--black));
  --surface: light-dark(var(--white), var(--black));
  --surface-hover: light-dark(var(--grey-1), var(--grey-7));
  --border: light-dark(var(--grey-2), var(--grey-7));
  --border-strong: light-dark(var(--grey-3), var(--grey-6));
  --text: light-dark(var(--black), var(--grey-1));
  --text-muted: light-dark(var(--grey-5), var(--grey-4));
  --accent: light-dark({accent}, {accent_dark});
  --accent-contrast: light-dark({accent_contrast(accent)}, {accent_contrast(accent_dark)});
  --accent-soft: color-mix(in srgb, var(--accent) 10%, transparent);
  --scrim: color-mix(in srgb, var(--black) 50%, transparent);

  /* Status semantics (locked, story 16.2-001): --danger alone carries hue,
     for failures/destructive actions only; pass/ok/warn are mono + weight. */
  --danger: light-dark({danger}, {danger_dark});
  --danger-soft: color-mix(in srgb, var(--danger) 10%, transparent);
  --ok-fg: var(--text-muted);
  --err-fg: var(--danger);
  --warn-fg: var(--text);
  --status-on: var(--accent);
  --status-off: var(--text-muted);
  --status-warn: light-dark(var(--grey-6), var(--grey-3));

  /* Chart layer (story 16.2-002) — axes/labels reuse the UI greys; series
     stops are dual-valued so data marks hold AA non-text contrast (3:1)
     against --bg in both schemes, with --chart-label holding the 4.5:1 text
     bar. The accent stays reserved for the highlighted series. */
  --chart-axis: var(--border-strong);
  --chart-grid: var(--border);
  --chart-label: var(--text-muted);
  --chart-accent: var(--accent);
  --chart-grey-1: light-dark(var(--grey-7), var(--grey-2));
  --chart-grey-2: light-dark(var(--grey-6), var(--grey-3));
  --chart-grey-3: light-dark(var(--grey-5), var(--grey-4));

  /* Comparison side colors (story 17.2-001) — the report view's "sides" speak
     the same accent-leads, greys-support language as the charts; cohorts past
     the fourth cycle these stops client-side. */
  --cmp-side-1: var(--chart-accent);
  --cmp-side-2: var(--chart-grey-1);
  --cmp-side-3: var(--chart-grey-2);
  --cmp-side-4: var(--chart-grey-3);

  /* Type scale. */
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, system-ui, sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --text-xs: 0.78rem;
  --text-sm: 0.875rem;
  --text-base: 0.95rem;
  --text-lg: 1.1rem;
  --text-xl: 1.4rem;

  /* Spacing scale. */
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-5: 1.5rem;
  --space-6: 2rem;
  --space-7: 3rem;

  /* Radii. */
  --radius-sm: 4px;
  --radius-md: 8px;
  --radius-full: 999px;

  /* Elevation — hairline rings only; no decorative drop shadows. */
  --elevation-0: none;
  --elevation-1: 0 0 0 1px var(--border);
  --elevation-2: 0 0 0 1px var(--border-strong);
}}
"""


TOKENS_CSS = tokens_css()
"""The token block for the shipped default theme."""


# --------------------------------------------------------------------------- #
# Mode overrides + toggle chrome (story 16.1-002). Forcing color-scheme on the
# root flips every light-dark() token at once and drags native controls and
# scrollbars along; the OS scheme stays in charge until [data-theme] is set.
# --------------------------------------------------------------------------- #

MODES_CSS = """\
:root[data-theme="light"] { color-scheme: light; }
:root[data-theme="dark"] { color-scheme: dark; }
#theme-toggle { position: fixed; top: var(--space-3); right: var(--space-3); z-index: 20;
  width: 2.1rem; height: 2.1rem; padding: 0; border-radius: var(--radius-full);
  background: var(--bg); font-size: var(--text-base); line-height: 1; }
@media (prefers-reduced-motion: no-preference) {
  body, #theme-toggle { transition: background-color 160ms ease, color 160ms ease; }
}
"""

THEME_STORAGE_KEY = "lcb-theme"

# Pre-paint script for <head>: applies the stored mode (else the configured
# default mode, story 16.4-001) before the first paint so a page never flashes
# the wrong theme. Written with __KEY__/__DEFAULT__ placeholders (not an
# f-string) so the JS braces stay literal.
_THEME_INIT_JS_TEMPLATE = """\
(function () {
  try {
    var stored = localStorage.getItem("__KEY__");
    var mode = stored === "light" || stored === "dark" ? stored : "__DEFAULT__";
    if (mode === "light" || mode === "dark") {
      document.documentElement.dataset.theme = mode;
    }
  } catch (err) { /* storage unavailable — stay on the OS scheme */ }
})();
""".replace("__KEY__", THEME_STORAGE_KEY)


def _theme_init_js(default_mode: str) -> str:
    return _THEME_INIT_JS_TEMPLATE.replace("__DEFAULT__", default_mode)


def theme_head_snippet(default_mode: str = DEFAULT_MODE) -> str:
    """The <head> pre-paint snippet: stored preference first, then the
    configured initial mode; ``system`` leaves the OS scheme in charge."""

    return f"<script>\n{_theme_init_js(default_mode)}</script>"


THEME_INIT_JS = _theme_init_js(DEFAULT_MODE)

THEME_TOGGLE_HTML = '<button id="theme-toggle" type="button"></button>'

# The toggle flips to the opposite of the *effective* mode (stored preference,
# else the OS scheme), persists the choice, and keeps its glyph/label in sync —
# including when the OS scheme changes while no preference is stored.
THEME_TOGGLE_JS = """\
(function () {
  var root = document.documentElement;
  var button = document.getElementById("theme-toggle");
  if (!button) { return; }
  var media = window.matchMedia("(prefers-color-scheme: dark)");
  function mode() {
    var forced = root.dataset.theme;
    if (forced === "light" || forced === "dark") { return forced; }
    return media.matches ? "dark" : "light";
  }
  function render() {
    var next = mode() === "dark" ? "light" : "dark";
    button.textContent = next === "light" ? "\\u2600" : "\\u263e";
    button.setAttribute("aria-label", "Switch to " + next + " theme");
    button.title = "Switch to " + next + " theme";
  }
  button.addEventListener("click", function () {
    var next = mode() === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("__KEY__", next); } catch (err) { /* not persisted */ }
    render();
  });
  media.addEventListener("change", render);
  render();
})();
""".replace("__KEY__", THEME_STORAGE_KEY)

# Ready-to-embed snippets for the shared page chrome: the pre-paint script goes
# in <head>, the toggle (button + behavior) right after <body>.
THEME_HEAD_SNIPPET = f"<script>\n{THEME_INIT_JS}</script>"
THEME_TOGGLE_SNIPPET = f"{THEME_TOGGLE_HTML}\n<script>\n{THEME_TOGGLE_JS}</script>"


# --------------------------------------------------------------------------- #
# Base styles — shared primitives rendered purely from the tokens above.
# --------------------------------------------------------------------------- #

BASE_CSS = """\
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: var(--font-sans);
  font-size: var(--text-base);
  line-height: 1.55;
  color: var(--text);
  background: var(--bg);
}
h1 { margin: 0 0 var(--space-2); font-size: var(--text-xl); font-weight: 650;
  letter-spacing: -0.015em; }
h2 { margin: var(--space-6) 0 var(--space-3); font-size: var(--text-lg); font-weight: 600; }
h3 { margin: var(--space-4) 0 var(--space-1); font-size: var(--text-base); font-weight: 600; }
a { color: var(--accent); }
table { border-collapse: collapse; width: 100%; font-size: var(--text-sm); }
th, td { text-align: left; padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--border); }
th { font-weight: 600; color: var(--text-muted); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
button { font: inherit; color: var(--text); background: transparent;
  border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-3); cursor: pointer; }
button:hover:not(:disabled) { background: var(--surface-hover); }
button:active:not(:disabled) { border-color: var(--accent); }
button:disabled { opacity: 0.4; cursor: default; }
button.act { border-color: var(--accent); color: var(--accent); }
button.act:hover:not(:disabled) { background: var(--accent-soft); }
button.danger { border-color: var(--danger); color: var(--danger); }
button.danger:hover:not(:disabled) { background: var(--danger-soft); }
button.danger:active:not(:disabled) { border-color: var(--danger); }
input, select, textarea { font: inherit; color: var(--text); background: var(--surface);
  border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-2); }
:is(button, input, select, textarea):focus-visible { outline: 2px solid var(--accent);
  outline-offset: 1px; }
.badge { display: inline-block; padding: 0 var(--space-2);
  border: 1px solid var(--border-strong); border-radius: var(--radius-full);
  font-size: var(--text-xs); color: var(--text-muted); }
.dot { display: inline-block; width: 1rem; text-align: center; line-height: 1; }
.dot.up::before { content: "●"; color: var(--status-on); }
.dot.down::before { content: "○"; color: var(--status-off); }
.dot.warn::before { content: "◐"; color: var(--status-warn); }
.pass, .ok { color: var(--ok-fg); font-weight: 600; }
.fail { color: var(--err-fg); font-weight: 600; }
.err, .bad { color: var(--err-fg); font-weight: 600; min-height: 1.2rem; }
p.warn, span.warn, li.warn { color: var(--warn-fg); font-weight: 600; min-height: 1.2rem; }
:is(.pass, .ok):not(:empty)::before { content: "✓ "; }
:is(.fail, .err, .bad):not(:empty)::before { content: "✕ "; }
:is(p, span, li).warn:not(:empty)::before { content: "! "; }
.empty { color: var(--text-muted); font-style: italic; }
.note { color: var(--text-muted); max-width: 44rem; line-height: 1.5; }
.progress:not(:empty) { color: var(--accent); font-weight: 600;
  font-variant-numeric: tabular-nums; }
#modal { position: fixed; inset: 0; background: var(--scrim); display: none;
  align-items: center; justify-content: center; }
#modal.show { display: flex; }
.card { background: var(--bg); color: var(--text); padding: var(--space-4) var(--space-5);
  border: 1px solid var(--border-strong); border-radius: var(--radius-md);
  max-width: 26rem; box-shadow: var(--elevation-2); }
.card ul { margin: var(--space-2) 0 var(--space-4); }
"""

def theme_css(config: ThemeConfig | None = None) -> str:
    """Tokens + mode overrides + base styles for a theme, ready for ``<style>``."""

    return tokens_css(config) + "\n" + MODES_CSS + "\n" + BASE_CSS


THEME_CSS = theme_css()
"""Tokens + mode overrides + base styles, ready to embed in a page's ``<style>`` block."""
