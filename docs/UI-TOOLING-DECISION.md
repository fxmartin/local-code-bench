# UI Tooling Decision Note — CSS/Chart Layer Evaluation (Story 16.3-001)

Time-boxed spike evaluating the shortlisted front-end tools — **Open Props**,
**Pico.css**, and **uPlot** — against the dashboard's hard constraints:
vendored (no CDN), fully offline serving, no build step, and composition with
the 16.1-001 design-token layer (`src/local_code_bench/theme.py`).

**Decision: all three candidates are rejected.** The hand-rolled token
approach proceeds unchanged. This is a deliberate outcome, not a deferral —
the evaluation below records why each tool's cost outweighs the control it
would add.

## Method

Each candidate's minified distribution file was downloaded at a pinned
version and measured (raw and gzip bytes). Baseline figures for comparison,
measured from this repo at evaluation time (2026-07-17):

| Baseline artifact | Size (bytes) |
| --- | ---: |
| `theme.THEME_CSS` (tokens + base styles, the whole 16.1 layer) | 4,577 |
| `theme.TOKENS_CSS` (token block alone) | 1,898 |
| Live results page (`dashboard_server.render_page()`) | 15,885 |
| Unified dashboard page (`unified_dashboard.render_page()`) | 60,138 |
| Inferencer panel (`inferencers.dashboard.render_page()`) | 8,159 |

All pages are self-contained HTML with embedded CSS; charts are inline SVG
generated in Python (`dashboard_charts.py`) with zero JavaScript
dependencies. Any vendored asset would be embedded into every rendered page
(or served as an extra file by the local process), so its full size lands on
the render-page weight either way.

## Candidate: Open Props

- **Evaluated version**: 1.7.16 (`open-props.min.css`)
- **Vendored size**: 27,601 bytes raw / 7,412 bytes gzip
- **Licence**: MIT — vendorable with attribution
- **Offline serving**: plain CSS file; trivially served by the local process
- **No-build compatibility**: yes — pure CSS custom properties, no
  preprocessor required (the sub-file imports like `open-props/colors` do
  assume a bundler or per-file vendoring, but the single minified bundle
  avoids that)
- **Control sharpness**: negative. Open Props is a *generic* token vocabulary:
  dozens of hue ramps, gradients, shadows, and easings. The 16.1 token layer
  is deliberately narrower — white/black anchors, one grey ramp, a single
  accent — and `tests/test_theme.py` enforces that shape by grepping every
  dashboard module for stray color literals and asserting exactly one accent
  hue. Importing Open Props would inject hundreds of unused color properties,
  break the "one accent" test invariant, and loosen the design constraint the
  epic exists to sharpen.
- **Composition with the 16.1 token layer**: poor. Its `--blue-6`-style raw
  palette overlaps the role of our palette anchors without providing our
  semantic layer (`--bg`, `--surface`, `--text-muted`, …), so we would keep
  all of `theme.py` and add 6× its size in unused vocabulary (27.6 KB vs the
  entire 4.6 KB hand-rolled layer).

**Outcome**: Reject — it replaces the cheapest part of the stack (naming
tokens) while weakening the enforced palette discipline.

## Candidate: Pico.css

- **Evaluated version**: 2.1.1 (`pico.min.css`)
- **Vendored size**: 83,319 bytes raw / 11,740 bytes gzip
- **Licence**: MIT — vendorable with attribution
- **Offline serving**: plain CSS file; trivially served by the local process
- **No-build compatibility**: yes as shipped; customizing its palette
  properly is documented through its Sass pipeline, which *would* reintroduce
  a build step we have banned
- **Control sharpness**: negative. Pico is a classless base-style framework:
  it styles the same primitives `theme.BASE_CSS` already covers (headings,
  tables, buttons, inputs) but through its own `--pico-*` custom properties
  and its own light/dark color scheme. Overriding those to match the
  monochrome-plus-accent design means re-declaring most of what we already
  have, on top of an 83 KB base — versus the 4.6 KB total we ship today.
- **Composition with the 16.1 token layer**: conflicting. Pico's stylesheet
  carries its own color literals, which violates the grep-enforced invariant
  that `theme.py` is the sole home of color values; every page embedding it
  would fail `test_no_color_literals_outside_theme_module`'s rendered-page
  variant unless the test were weakened. The live results page would grow
  from 15.9 KB to ~99 KB (6×) for styling we already control more tightly.

**Outcome**: Reject — duplicate coverage of already-styled primitives at 18×
the size of the entire current theme layer, with a token system that fights
ours.

## Candidate: uPlot

- **Evaluated version**: 1.6.32 (`uPlot.iife.min.js` + `uPlot.min.css`)
- **Vendored size**: 51,081 bytes JS + 1,857 bytes CSS raw / 22,016 + 772
  bytes gzip
- **Licence**: MIT — vendorable with attribution
- **Offline serving**: yes — self-contained IIFE bundle, no CDN or fetch at
  runtime
- **No-build compatibility**: yes — drop-in `<script>` tag
- **Control sharpness**: real but unneeded. uPlot's strengths are
  high-frequency time series (hundreds of thousands of points), zoom/pan, and
  cursor sync. The dashboard charts plot a handful of models and sweep
  points; the Python-side inline SVG already renders them instantly with
  hover tooltips. Interactivity gains do not currently justify the costs
  below.
- **Composition with the 16.1 token layer**: workable for colors (series
  colors could be fed from `theme.CHART_SERIES`), but adoption would move
  chart rendering from unit-testable Python (`dashboard_charts.py`'s pure
  point-selection functions and SVG assembly) into client-side JavaScript
  that our pytest suite cannot exercise, and would add the first mandatory
  JS dependency to pages that are currently zero-JS-required. It would also
  put a canvas-rendering seam between the chart data model and the page,
  where story 16.2-002 requires the existing chart data seams
  (`cost_quality_points`, `quality_speed_points`, `sweep_series`) to stay
  tool-agnostic — those seams remain untouched by this decision, so a future
  re-evaluation (e.g. if sweep runs grow to thousands of points) can slot
  uPlot in behind them without reworking 16.2 output.

**Outcome**: Reject — at today's data volumes it trades Python-side
testability and the zero-JS offline guarantee for interactivity we don't
need; the preserved data seams keep the door open if volumes change.

## Consequences

- The hand-rolled token layer (`theme.py`) remains the single styling source;
  no vendored assets, licence files, or pinned third-party versions enter the
  repo.
- `tests/test_ui_tooling_decision.py` enforces this note's shape and asserts
  that no rejected candidate is vendored into the package.
- Revisit triggers: sweep datasets large enough that inline SVG becomes slow
  to render or navigate (uPlot), or a second themed surface family that makes
  the token vocabulary genuinely insufficient (Open Props).
