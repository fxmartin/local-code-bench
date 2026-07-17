# Epic 17: Benchmark Comparison Dashboard (Benchmarks Tab + PDF Export)

## Epic Overview
**Epic ID**: Epic-17
**Description**: Add a **Benchmarks tab** to the unified dashboard (Epic-09): a report-style comparison surface in the spirit of the oMLX community benchmark pages FX referenced — an editorial hero ("A vs B" with per-side colors), methodology chips from run metadata, paired stat tiles and bars, a speed-vs-correctness frontier, and **written, rule-derived conclusions** with the numbers inline. The tab is driven by a declared **comparison-axis catalog** (dense vs MoE vs sparse-MoE, size ladder, q4 vs q8, mlx-lm vs Ollama, context scaling, specialized vs general, local vs cloud — see the proposition below), aggregated from the same re-scorable `results/*.jsonl` the leaderboard uses, with every number traceable to the runs that produced it. A **Download PDF** button exports the current comparison as a print-faithful document via a dedicated print stylesheet plus a detect-only headless-Chrome renderer (the harness never installs tools — same philosophy as the inferencer registry), with browser print-to-PDF as the universal fallback.
**Business Value**: The harness answers FX's questions today only in raw form: `LEADERBOARD.md` ranks, the dashboard operates, but nothing *tells the story* — is agentic coding still prefill-bound? did MoE actually move prefill or only decode? what did q8 buy over q4? which engine wins? Those are the findings the whole project exists to produce (they end up in `docs/FINDINGS.md` and the article series), and today each one is assembled by hand from JSONL spelunking. A comparison dashboard turns a finished run matrix into publishable evidence in one click — and the PDF export makes a benchmark campaign shareable with people who will never run the harness.
**Success Metrics**: From the Benchmarks tab FX can pick any declared comparison axis and see, without touching a terminal: the paired hero, per-model prefill/decode/TTFT/pass@1/cost panels, the Pareto frontier, and deterministic conclusion callouts each carrying its supporting numbers and run IDs; axes with insufficient or stale data say so explicitly rather than rendering misleading charts; the comparison catalog lives in YAML (nothing hardcoded) so a new axis is config, not code; and Download PDF produces a self-contained, print-faithful document (forced light theme, page headers with run metadata and date) via detected Chrome, or falls back to guided browser print when no renderer is present.

## Epic Scope
**Total Stories**: 6 | **Total Points**: 24 | **MVP Stories**: 0 (Should Have / v1.x)

## The Comparison Proposition (axes and the conclusions they support)

Each axis is a *cohort pairing* over the existing model matrix (`configs/models.yaml`) plus a *verdict rule* — a deterministic computation whose output is a written conclusion with its numbers. The proposed v1 catalog:

| # | Axis | Cohorts (from the current matrix) | Conclusions it can draw |
|---|------|-----------------------------------|--------------------------|
| 1 | **Engine: mlx-lm vs Ollama** | Every `local-mlx-*`/`local-ollama-*` pair; **gpt-oss-20b flagged as the clean A/B** (identical native MXFP4 weights on both engines — all other pairs compare different quant artifacts too) | "Engine X is Y% faster at prefill and Z% at decode for the same weights"; per-format engine recommendation; whether the MLX advantage holds across architectures |
| 2 | **Architecture: dense vs MoE vs sparse-MoE** | Dense: Qwen3.6-27B, Devstral-24B · MoE (~3B active): Qwen3.6-35B-A3B, Qwen3-Coder-30B, Ornith-35B, gpt-oss-20b · Sparse-MoE (512-expert): Qwen3-Coder-Next-80B — with **Qwen3.6-27B vs Qwen3.6-35B-A3B highlighted as the same-generation controlled pair** | The headline thesis check: "MoE moves decode N× but prefill only M× — agentic coding remains prefill-bound (or not)"; quality-at-equal-decode-speed across architectures |
| 3 | **Size / capability ladder** | ~20B (gpt-oss) → 24B (Devstral) → 27B dense → 30B/35B MoE → 80B-A3B; optionally extend downward with Ornith-1.0-9B (dense, ~5 GB) if FX wants the 9B rung populated | "Smallest model clearing the quality bar"; where pass@1 saturates vs where tok/s collapses; quality per GB of unified memory |
| 4 | **Quantization: q4 vs q8 (and 3-bit stretch)** | Qwen3.6-35B-A3B q4/q8 on both engines (the purpose-built pairs); Qwen3-Coder-Next 3-bit vs Qwen3-Coder-30B 4-bit as "aggressive-quant big MoE vs clean-quant small MoE" | "q8 buys +X pp pass@1 for −Y% decode and +Z GB"; whether 3-bit degradation is visible on HumanEval/MBPP; the DFlash/TurboQuant-style verdict for FINDINGS.md |
| 5 | **Context scaling (prefill-vs-context)** | Epic-05 sweep runs per model/engine: TTFT and prefill tok/s as context grows | The reference articles' curve reproduced per model: "TTFT grows ~linearly at N ms/KB on mlx-lm vs M on Ollama"; which setups stay usable at agentic context sizes |
| 6 | **Specialized vs general** | Qwen3-Coder-30B vs Qwen3.6-35B-A3B (coding-tuned vs general, same class); Devstral vs Qwen3.6-27B (dense pair) | "Coding specialization is worth +X pp pass@1 at equal speed (or is not)" |
| 7 | **Local vs cloud frontier** | Best local setups vs GLM-4.6, Kimi K2, Qwen3.6-27B cloud, Claude baseline — including $/solved-task (local $0 + wall-time vs cloud $) | "The gap to frontier is X pp pass@1 and the cloud premium is $Y per solved task"; when local is rationally preferable |

Cross-cutting views available on every axis: the **Pareto frontier** scatter (pass@1 vs decode tok/s, point size = memory footprint, accent = frontier members) and **canary drift** (the fixed anchor subset over time, flagging when a config change moved quality).

## Decisions Locked With FX
- **New tab** in the unified dashboard (not a separate tool), report-style presentation modeled on the oMLX community benchmark pages.
- **Download PDF button** is in scope from v1.
- **Conclusions are deterministic** — rule-based computations over aggregates, never an LLM writing prose; every conclusion shows its numbers and links its run IDs.

## Decisions To Confirm With FX
- **Catalog v1 cut**: all seven axes above, or a smaller launch set (proposal: 1, 2, 4, 7 first — they have complete data the moment the current matrix finishes one full suite run; 5 needs sweep runs, 3 benefits from adding a 9B, 6 falls out of the same runs as 2).
- **The quality bar** used by "smallest model clearing the bar" verdicts (proposal: pass@1 within 5 pp of the best local model on the same suite; a `benchmark_dashboard.quality_bar` value in `configs/settings.yaml` per nothing-hardcoded).
- **Comparison hero colors**: the reference uses one color per side (cyan/pink). Under the locked Epic-16 palette (greys + dark blue + dark red, red reserved for failures), the proposal is side A = accent blue, side B = a mid-grey — strict. Alternative needing sign-off: a second comparison hue used only inside the Benchmarks tab's charts.

## Scope Boundaries (explicitly NOT building)
- **No new measurements** — the tab visualizes existing `results/*.jsonl`; it never launches runs (the Run section already does that). An axis without data renders as "no comparable runs yet", listing what to run.
- **No LLM-generated analysis** — conclusions are deterministic rules; prose templates with computed numbers.
- **No public publishing pipeline** — PDF/HTML export lands on disk for FX to share; no upload, no hosting, no artifact publishing.
- **No PDF library bundling** — the PDF path is detect-only (system Chrome/Chromium) with browser-print fallback; the harness installs nothing, consistent with the inferencer philosophy.

## Design Reference
- **Aggregation before presentation**: a pure comparison module turns run JSONL into per-configuration summary stats (median/p95 TTFT, prefill/decode tok/s, pass@1, cost/task, footprint, run metadata) keyed by the Epic-11 `base_model_key` so the same nominal model pairs up across engines and quants. The tab is a thin client over `GET /api/compare?axis=...`.
- **Axis catalog as config**: `configs/comparisons.yaml` — each axis declares cohort filters (match on model name/inferencer/quant/tags), the pairing key, highlighted "controlled pairs", and its verdict rules with thresholds. Nothing-hardcoded: adding axis #8 is YAML.
- **Traceability**: every tile, bar, and conclusion carries the run IDs (and suite/version/hardware tag) it derives from; mixed-suite or mixed-hardware comparisons are refused per axis rather than silently blended — reproducibility metadata (Epic-01/03) is the join guard.
- **Report styling**: the tab is the first consumer of the Epic-16 token system's "report" idiom — monospace kicker, two-sided hero, stat tiles, restrained grid — and must render correctly in both modes; PDF forces light mode.
- **PDF path**: `@media print` stylesheet (page size, breaks between sections, header/footer with title, date, suite version, hardware tag) + `POST /api/report-pdf` shelling to a *detected* Chrome/Chromium `--headless --print-to-pdf` against the server's own localhost URL, returning the file as a download into `results/reports/`. No Chrome detected → the button explains the browser print-to-PDF path, which uses the same print stylesheet.

## Features in This Epic

### Feature 17.1: Comparison Data & Axis Catalog

#### Stories

##### Story 17.1-001: Comparison aggregation module and API
**User Story**: As FX, I want the harness to aggregate my raw run files into paired, comparable per-configuration statistics, so that every comparison in the tab is computed from the same re-scorable data as the leaderboard — never a hand-picked number.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** `results/*.jsonl` **When** the comparison aggregate builds **Then** each model/engine/quant configuration yields summary stats — median and p95 TTFT, prefill tok/s, decode tok/s, total latency, pass@1 per suite, cost per task, memory footprint (from inventory where known) — with the run IDs, suite version, and hardware tag attached.
- **Given** the same nominal model across engines or quants **When** aggregated **Then** configurations pair up via the Epic-11 `base_model_key` normalization, and the gpt-oss identical-weights pair is flaggable as a controlled comparison.
- **Given** runs from different suites, suite versions, or hardware tags **When** a comparison is requested **Then** incomparable runs are excluded with an explicit reason, never silently averaged.
- **Given** `GET /api/compare?axis=<id>` **When** called **Then** it returns the axis's cohorts, paired stats, verdict inputs, and per-number provenance as JSON; unknown axis → 404.

**Technical Notes**: Pure module (e.g. `src/local_code_bench/compare.py`) reusing the results loaders (Epic-04 rescore path) and `inventory.base_model_key`; the dashboard action layer stays thin per house style. Medians over means throughout (flaky-run tolerance).

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 04.2-001, 09.1-001
**Risk Level**: Medium

##### Story 17.1-002: Comparison-axis catalog in YAML
**User Story**: As FX, I want the comparison axes declared in `configs/comparisons.yaml`, so that the seven proposed comparisons ship as data and an eighth is a config edit, per the nothing-hardcoded principle.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** the shipped catalog **When** loaded **Then** it declares the seven proposition axes with: id, title, the two (or N) cohort filters, pairing key, highlighted controlled pairs, and verdict rules with their thresholds.
- **Given** a malformed axis **When** the catalog loads **Then** that axis is rejected with a clear loader error naming the field; valid axes still load.
- **Given** an axis whose cohort filters match no configured models or no runs **When** rendered **Then** the tab shows the axis with a "no comparable runs yet" state listing which models/suites would populate it.
- **Given** the Epic-15 Settings tab **When** it lands **Then** the catalog is one more registered config surface (read view at minimum) with no extra work here beyond using the standard loader shape.

**Technical Notes**: Loader in `config.py` following the existing block patterns; filters match on model name globs, `inferencer`, quant token, and explicit name lists. Verdict thresholds (e.g. the quality bar) resolve through the 15.5 settings layer once it exists, with shipped defaults until then.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 17.1-001
**Risk Level**: Low

### Feature 17.2: The Benchmarks Tab

#### Stories

##### Story 17.2-001: Report-style comparison view
**User Story**: As FX, I want a Benchmarks tab that renders a chosen axis as a designed report — hero, methodology chips, paired panels, frontier chart — so that a finished run matrix reads as evidence, not as tables to interpret.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the Benchmarks tab **When** opened **Then** an axis picker lists the catalog (data-ready axes first, empty ones marked), and selecting one renders: a two-sided hero (side names in the agreed comparison colors), a subtitle stating the controlled variables, and methodology chips (engine versions, suite + version, seed/temp, hardware tag, run dates) from run metadata.
- **Given** the selected axis **When** rendered **Then** each cohort member shows paired stat panels (prefill, decode, TTFT, pass@1, cost/task) with side-colored bars, and controlled pairs (same-generation, identical-weights) are visibly badged.
- **Given** any axis **When** rendered **Then** the cross-cutting Pareto frontier (pass@1 vs decode tok/s, size-scaled points, accent-marked frontier) and, where sweep data exists, the context-scaling curve are available as sections.
- **Given** the Epic-16 theme **When** applied **Then** the tab renders from the token system in both modes with no new raw color literals (the comparison side colors are tokens).

**Technical Notes**: Thin client over `/api/compare`; charts follow the 16.2-002 chart-token approach (and uPlot if 16.3-001 adopted it). The hero/tile/kicker components should be built as reusable primitives — they are the "report idiom" future surfaces (and the PDF) reuse.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 17.1-002, 16.1-001, 16.2-002
**Risk Level**: Medium

##### Story 17.2-002: Deterministic conclusion callouts
**User Story**: As FX, I want each axis to render written conclusions computed by its verdict rules — "MoE moved decode 3.4× but prefill only 1.2×: still prefill-bound" — so that the dashboard states findings I can lift straight into FINDINGS.md, with the evidence attached.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** an axis with sufficient data **When** rendered **Then** its verdict rules produce conclusion callouts as templated prose with the computed numbers inline, each listing its supporting run IDs and the threshold it applied.
- **Given** insufficient or one-sided data for a rule **When** evaluated **Then** the callout states what is missing ("needs a q8 run of X on ollama") instead of concluding from partial data.
- **Given** a conclusion near its threshold (within a declared margin) **When** rendered **Then** it is phrased as "inconclusive — within noise margin", never as a confident verdict.
- **Given** the canary axis view **When** rendered **Then** drift beyond the declared tolerance versus the previous run is called out with both values and dates.

**Technical Notes**: Verdict rules are pure functions over the 17.1-001 aggregates, declared per axis in the catalog (rule id + params); prose lives in templates keyed by rule id. This is the layer that must never overreach: silence over spin.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 17.2-001
**Risk Level**: Medium

### Feature 17.3: PDF Export

#### Stories

##### Story 17.3-001: Print-faithful report stylesheet
**User Story**: As FX, I want the rendered comparison to carry a dedicated print stylesheet, so that the report paginates cleanly — whether exported by the PDF button or by the browser's own print-to-PDF.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** the Benchmarks tab **When** printed (`@media print`) **Then** the report forces the light token set, hides all dashboard chrome (nav, pickers, buttons), and paginates with breaks between sections — no orphaned hero, no split stat tiles or truncated charts.
- **Given** each printed page **When** rendered **Then** it carries a header/footer with the report title, generation date, suite + version, and hardware tag.
- **Given** charts **When** printed **Then** they render at print resolution with legible labels (canvas charts re-rendered at print DPI or exported as SVG for print).
- **Given** the print output **When** compared to the screen report **Then** every number and conclusion is present — print is a faithful projection, not a summary.

**Technical Notes**: Print styles are part of the Epic-16 token layer's output (light values forced via the print media query, not a second theme). Chart print fidelity is the risky corner — prefer SVG rendering for report charts if 16.2-002 hasn't already settled it.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 17.2-001, 16.1-001
**Risk Level**: Medium

##### Story 17.3-002: Download-PDF button via detected Chrome, with guided fallback
**User Story**: As FX, I want a Download PDF button on the Benchmarks tab that produces the file in one click, so that sharing a benchmark campaign is a download, not a print-dialog ritual.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** Chrome/Chromium is detected on the machine (detect-only, like inferencer detection — the harness never installs it) **When** Download PDF is clicked **Then** the server renders the current axis view via `--headless --print-to-pdf` against its own localhost URL and returns the file as a download, also archiving a copy under `results/reports/<axis>-<date>.pdf`.
- **Given** no renderer is detected **When** the button is clicked **Then** it explains the browser print-to-PDF path (same print stylesheet, same output) instead of failing silently, and the settings show what binary would enable one-click export.
- **Given** a PDF render in progress **When** requested again **Then** the second request is refused while the first runs (same one-at-a-time convention as tier moves), with progress shown on the button.
- **Given** the archived report **When** inspected **Then** its filename and embedded metadata identify axis, date, suite version, and hardware tag; the endpoint binds localhost only and the PDF contains no paths beyond the report content.

**Technical Notes**: Renderer detection follows the Epic-08 detect pattern (binary lookup: `google-chrome`, `chromium`, macOS app-bundle path for Chrome/Chromium — configurable per nothing-hardcoded in `settings.yaml`). The render URL includes a print token/param so the server serves the print projection directly (no auth complexity — localhost only). Subprocess with timeout; failure surfaces the stderr tail. `results/reports/` is gitignored like the rest of `results/`.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 17.3-001
**Risk Level**: High

## Epic Progress
**Completed**: 0 / 6 stories · 0 / 24 points
