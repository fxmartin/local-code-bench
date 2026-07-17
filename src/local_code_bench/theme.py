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
  gradients or drop shadows.

Pages embed ``THEME_CSS`` (tokens + base) and keep only layout-specific rules
of their own, expressed exclusively as ``var(...)`` references. Restyling the
UI is therefore a token edit here, not a hunt through per-page hex literals.
"""

from __future__ import annotations

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

# Single accent, tuned to hold ~4.5:1 contrast on both the white and the black
# anchor so one literal serves both color schemes.
ACCENT = "#3a6df0"

# Categorical data-series colors for the inline SVG charts. These are a
# data-visualization layer on top of the UI palette (distinguishing up to eight
# sweep series needs hue, which a monochrome ramp cannot provide); the first
# series is the accent so single-series charts stay within the palette.
CHART_SERIES = (
    ACCENT,
    "#ff9f0a",
    "#30d158",
    "#bf5af2",
    "#ff375f",
    "#64d2ff",
    "#ffd60a",
    "#a2845e",
)


# --------------------------------------------------------------------------- #
# Token block — the sole home of color literals in any stylesheet.
# --------------------------------------------------------------------------- #

TOKENS_CSS = f"""\
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
  --accent: {ACCENT};

  /* Semantic colors — component rules resolve only through these. */
  --bg: light-dark(var(--white), var(--black));
  --surface: light-dark(var(--white), var(--black));
  --surface-hover: light-dark(var(--grey-1), var(--grey-7));
  --border: light-dark(var(--grey-2), var(--grey-7));
  --border-strong: light-dark(var(--grey-3), var(--grey-6));
  --text: light-dark(var(--black), var(--grey-1));
  --text-muted: light-dark(var(--grey-5), var(--grey-4));
  --accent-soft: color-mix(in srgb, var(--accent) 10%, transparent);
  --scrim: color-mix(in srgb, var(--black) 50%, transparent);

  /* Status semantics, mapped onto the palette (mono + weight, not extra hues). */
  --ok-fg: var(--text-muted);
  --err-fg: var(--text);
  --warn-fg: var(--text-muted);
  --status-on: var(--accent);
  --status-off: var(--border-strong);
  --status-warn: var(--text-muted);

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
button:disabled { opacity: 0.4; cursor: default; }
input, select, textarea { font: inherit; color: var(--text); background: var(--surface);
  border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-2); }
:is(button, input, select, textarea):focus-visible { outline: 2px solid var(--accent);
  outline-offset: 1px; }
.badge { display: inline-block; padding: 0 var(--space-2);
  border: 1px solid var(--border-strong); border-radius: var(--radius-full);
  font-size: var(--text-xs); color: var(--text-muted); }
.dot { display: inline-block; width: 0.65rem; height: 0.65rem;
  border-radius: var(--radius-full); }
.dot.up { background: var(--status-on); }
.dot.down { background: var(--status-off); }
.dot.warn { background: var(--status-warn); }
.pass, .ok { color: var(--ok-fg); }
.fail { color: var(--err-fg); font-weight: 600; }
.err, .bad { color: var(--err-fg); font-weight: 600; min-height: 1.2rem; }
p.warn, span.warn { color: var(--warn-fg); min-height: 1.2rem; }
.empty { color: var(--text-muted); font-style: italic; }
.note { color: var(--text-muted); max-width: 44rem; line-height: 1.5; }
#modal { position: fixed; inset: 0; background: var(--scrim); display: none;
  align-items: center; justify-content: center; }
#modal.show { display: flex; }
.card { background: var(--bg); color: var(--text); padding: var(--space-4) var(--space-5);
  border: 1px solid var(--border-strong); border-radius: var(--radius-md);
  max-width: 26rem; box-shadow: var(--elevation-2); }
.card ul { margin: var(--space-2) 0 var(--space-4); }
"""

THEME_CSS = TOKENS_CSS + "\n" + BASE_CSS
"""Tokens + base styles, ready to embed in a page's ``<style>`` block."""
