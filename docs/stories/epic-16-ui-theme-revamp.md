# Epic 16: Dashboard UI Revamp — Minimalist Monochrome Theme

## Epic Overview
**Epic ID**: Epic-16
**Description**: Restyle every dashboard surface — the unified dashboard (Epic-09) first, plus the static results page (Epic-07) and the standalone inferencer panel (Epic-08) that share its look — onto one **modern, minimalist theme**: black, white, and a disciplined grey scale, exactly **one accent color**, and first-class **light and dark modes**. The work replaces today's ~60 scattered hardcoded hex values (ad-hoc greens/reds/ambers per section) with a single design-token layer (CSS custom properties for color, typography, spacing, radius, elevation), restyles every component onto those tokens, brings the charts into the same monochrome-plus-accent language, and makes the theme configurable — accent color and default mode live in `configs/settings.yaml` per the Epic-15 nothing-hardcoded principle, editable from the Settings tab. Additional front-end libraries/tools are explicitly on the table where they buy sharper control, under a hard constraint: everything is vendored and served from the local Python process — no CDN, no network, no mandatory build step.
**Business Value**: The dashboard grew section by section (results, inferencers, launcher, chat, inventory, tiers) and looks like it: inconsistent colors, per-section one-off styles, a dark mode that is whatever the browser improvises via `color-scheme` rather than a designed theme. FX lives in this surface while running benchmark campaigns — often at night, where an undesigned dark mode is actively unpleasant — and every new section (Epic-15's Settings tab is next) currently invents its own styling. One token layer makes the whole surface coherent, makes dark mode deliberate, makes the next section cheap to style correctly, and makes the look a setting rather than a constant.
**Success Metrics**: Every dashboard surface renders from the shared token layer with zero hardcoded color literals outside the token definitions; light and dark modes both meet WCAG AA contrast for text, controls, and chart elements, follow the OS preference by default, and can be toggled manually with the choice persisted and no flash of the wrong theme on load; exactly one accent color appears across the UI (interactive/focus/highlight states), changeable in `configs/settings.yaml` and the Settings tab without touching source; charts read clearly in both modes using greys plus the accent; and any adopted library is vendored, licence-checked, and adds no network dependency or mandatory build step.

## Epic Scope
**Total Stories**: 6 | **Total Points**: 22 | **MVP Stories**: 0 (Should Have / v1.x)

## Decisions Locked With FX
- **Palette**: black / white / grey scale + **one single accent color**, nothing else.
- **Modes**: both light and dark, fully designed (not browser-default `color-scheme` rendering).
- **Style direction**: modern and minimalist.
- **Tooling**: additional libraries/tools are acceptable where they give sharper control.

## Decisions To Confirm With FX
- **The accent color itself**: proposal — ship a restrained electric blue as the default and make it a validated `theme.accent` hex in `configs/settings.yaml` (Epic-15), so the "one color" is FX's choice, not the theme's.
- **Status semantics under a one-accent palette**: today pass/fail/warn are green/red/amber. Proposal: status is conveyed by monochrome weight + iconography/text (✓/✗ glyphs, bold/dim), with the accent reserved for interactive and emphasis states — strict but truest to the brief. Alternative (needs explicit sign-off, since it bends "one single other color"): permit a functional red strictly for failures/destructive actions.
- **Library shortlist** (evaluated in 16.3-001, all vendorable, no build step): **Open Props** (design-token library, aligns exactly with the token approach), **Pico.css** (classless base for forms/tables), **uPlot** (~40 KB canvas charts if sharper chart control is wanted than the current hand-rolled drawing). Adopting zero of them and staying hand-rolled is an acceptable outcome of the evaluation.

## Scope Boundaries (explicitly NOT building)
- **No information-architecture redesign** — same sections, same features, same interaction flows; this epic changes how the dashboard *looks*, not what it does.
- **No SPA/framework rewrite** — the dashboard stays a server-rendered page with vanilla JS; React/Vue/Svelte and JS build pipelines are out.
- **No CDN or network assets** — every stylesheet/font/library ships vendored in the repo and is served by the localhost process; the benchmark machine must render perfectly offline. System font stack preferred over webfonts.
- **No per-user theming profiles** — one theme, two modes, one configurable accent; no theme galleries.

## Design Reference
- **Token layer**: one `<style>`/asset block defining semantic CSS custom properties — `--bg`, `--surface`, `--surface-raised`, `--border`, `--text`, `--text-muted`, `--accent`, `--accent-contrast`, plus spacing/type/radius/shadow scales — with dual values via `:root` (light) and `[data-theme="dark"]` overrides. Components reference only semantic tokens, never raw values; the grey scale is defined once and consumed indirectly.
- **Mode resolution**: default follows `prefers-color-scheme`; a header toggle stamps `data-theme` on `<html>` and persists to `localStorage`; an inline pre-paint script applies the stored choice before first render (no flash). `color-scheme` is kept in sync so native form controls and scrollbars match.
- **Accent discipline**: the accent appears only where it means something — primary actions, focus rings, active tab, links, chart highlight series, live-progress fill. Greys carry everything else.
- **Configurable theme (nothing hardcoded, Epic-15)**: `theme:` block in `configs/settings.yaml` (`accent`, `default_mode`), validated (hex color, `light|dark|system`), injected as the `--accent` token at page render; editable in the Settings tab via the 15.2 write pipeline.
- **Serving**: assets either stay embedded in the rendered page (as today) or move to a couple of local static routes — whichever the 16.3 evaluation favors; both respect the no-network rule.

## Features in This Epic

### Feature 16.1: Design Tokens & Modes

#### Stories

##### Story 16.1-001: Design-token layer and base styles
**User Story**: As FX, I want one design-token layer (monochrome scale + single accent + type/spacing scales) that every dashboard style derives from, so that the UI is coherent by construction and restyling is a token edit, not a hunt through sixty hex literals.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the rendered dashboard **When** its styles load **Then** all colors resolve from semantic CSS custom properties (`--bg`, `--surface`, `--border`, `--text`, `--text-muted`, `--accent`, …) defined in one place, and no component rule contains a raw hex/rgb literal.
- **Given** the token definitions **When** reviewed **Then** the palette is exactly: white/black anchors, a single grey ramp (5–7 steps), and one accent — with type scale, spacing scale, radius, and elevation tokens alongside.
- **Given** the base styles **When** applied **Then** shared primitives (page shell, headings, tables, buttons, inputs, badges, status lines) render from the tokens with a modern minimalist feel: generous whitespace, system font stack, hairline borders, restrained radii, no decorative gradients/shadows.
- **Given** the older surfaces (static results page, standalone inferencer panel) **When** rendered **Then** they consume the same token block rather than their own color literals.

**Technical Notes**: Tokens authored as CSS custom properties in one shared Python constant/asset consumed by every page template (`unified_dashboard.py`, `dashboard.py`, `dashboard_server.py`, `inferencers/dashboard.py`). Grep-enforceable AC: a test asserts no hex literals outside the token block. If 16.3-001 adopts Open Props, the ramp maps onto its variables; the semantic layer stays ours either way.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 09.1-001
**Risk Level**: Medium

##### Story 16.1-002: Designed light and dark modes with persistent toggle
**User Story**: As FX, I want properly designed light and dark modes — following my OS by default, switchable from the header, remembered across sessions — so that the dashboard is comfortable in a bright office and during a midnight benchmark run alike.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** no stored preference **When** the dashboard loads **Then** it renders in the mode matching `prefers-color-scheme`, with every token dual-valued (designed dark greys, not inverted or browser-default colors).
- **Given** the header mode toggle **When** clicked **Then** the theme switches instantly without reload, the choice persists in `localStorage`, and native controls/scrollbars follow via `color-scheme`.
- **Given** a stored preference **When** any dashboard page loads **Then** the stored mode applies before first paint — no flash of the wrong theme.
- **Given** either mode **When** audited **Then** text, controls, focus indicators, and status glyphs meet WCAG AA contrast against their backgrounds.

**Technical Notes**: `[data-theme]` attribute override pattern over the 16.1-001 tokens; tiny inline pre-paint script in `<head>`; toggle is part of the shared page chrome so all surfaces get it. Respect `prefers-reduced-motion` for any transition added.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 16.1-001
**Risk Level**: Low

### Feature 16.2: Component & Chart Restyle

#### Stories

##### Story 16.2-001: Restyle every section onto the token system
**User Story**: As FX, I want every dashboard section — results, inferencers, launcher/run monitor, chat, inventory, storage tiers, and the upcoming Settings tab — restyled onto the shared tokens, so that the whole surface reads as one designed product.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** each section **When** rendered **Then** its tables, forms, buttons, badges, tabs, and status/progress lines use only shared primitives and semantic tokens; all per-section ad-hoc color rules (today's `.up`/`.down`/`.pass`/`.fail`/`.warn` greens/reds/ambers) are replaced per the agreed status-semantics decision.
- **Given** pass/fail/status indicators **When** rendered monochrome **Then** state remains unambiguous without color (glyph + weight + text), verified with a no-color squint test in both modes.
- **Given** interactive elements **When** used **Then** hover/active/focus/disabled states are consistent everywhere, with the accent carrying focus and primary emphasis (including the tier-move live progress from 12.6-003).
- **Given** empty, loading, and error states in any section **When** shown **Then** they use one shared pattern (muted text, consistent placement) rather than per-section improvisation.

**Technical Notes**: Mostly a systematic sweep of the embedded HTML/CSS in `unified_dashboard.py` (and the two smaller pages) replacing literals with tokens and consolidating duplicate rules; the JS that stamps status classes keeps its class names — only the CSS meaning changes. The 12.6-003 progress line and chat bubbles are the two most bespoke spots.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 16.1-001, 16.1-002
**Risk Level**: Medium

##### Story 16.2-002: Charts in the monochrome-plus-accent language
**User Story**: As FX, I want the results/sweep charts to follow the theme — grey series, accent highlight, mode-aware axes — so that data visuals look like part of the product and stay readable in both modes.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** any chart **When** rendered **Then** axes, gridlines, labels, and series colors come from the token layer and adapt when the mode toggles (no repaint-on-reload requirement).
- **Given** multiple series under a near-monochrome palette **When** drawn **Then** series are distinguishable via the grey ramp plus line style/markers, with the accent reserved for the highlighted/selected series.
- **Given** both modes **When** audited **Then** chart text and marks meet the same AA contrast bar as the rest of the UI.

**Technical Notes**: Applies to the current hand-rolled chart drawing, or to uPlot if 16.3-001 adopts it — the token-sourced palette is the same either way. Charts should read theme tokens at draw time (getComputedStyle) so a mode toggle redraws correctly.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 16.1-002
**Risk Level**: Low

### Feature 16.3: Tooling Evaluation (Sharper Controls)

#### Stories

##### Story 16.3-001: Evaluate and (maybe) vendor a CSS/chart tooling layer
**User Story**: As FX, I want a deliberate evaluation of the shortlisted front-end tools (Open Props, Pico.css, uPlot) against the vendored/offline/no-build constraints, so that we adopt sharper controls only where they pay for themselves.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** each candidate **When** evaluated **Then** the outcome (adopt/reject) is recorded in a short decision note covering: control sharpness gained, vendored size, licence, offline serving, no-build compatibility, and how it composes with the 16.1 token layer.
- **Given** an adopted library **When** integrated **Then** it is vendored into the repo, served by the local process, licence-attributed, and pinned (file + version recorded); the dashboard renders fully with the machine offline.
- **Given** all candidates rejected **When** the evaluation ends **Then** that is a valid outcome and the hand-rolled token approach proceeds unchanged.

**Technical Notes**: Time-boxed spike. Vendored assets can live as package data or embedded strings — measure the render-page size impact either way. Any chart-library adoption must keep the existing chart data seams so 16.2-002 is tool-agnostic.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 16.1-001
**Risk Level**: Low

### Feature 16.4: Configurable Theme (Nothing Hardcoded)

#### Stories

##### Story 16.4-001: Theme settings in `settings.yaml` and the Settings tab
**User Story**: As FX, I want the accent color and default mode to live in `configs/settings.yaml` and be editable from the Settings tab, so that the theme obeys the nothing-hardcoded principle like every other operational value.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a `theme:` block (`accent: "#RRGGBB"`, `default_mode: light|dark|system`) **When** the dashboard renders **Then** the accent token and initial mode come from it, with the shipped defaults applying when the block is absent (additive, never breaking).
- **Given** an invalid value (malformed hex, unknown mode) **When** the config loads **Then** it is rejected with a clear loader error (via the 15.2 pipeline when edited from the tab), never rendered as a broken theme.
- **Given** the Settings tab's Harness/theme group **When** the accent is changed and saved **Then** the dashboard reflects it on next refresh without a restart, and the value round-trips through the validated write path.
- **Given** an accent with poor contrast against either mode's background **When** validated **Then** the editor warns (AA check against both `--bg` values) but does not block — FX owns the final call.

**Technical Notes**: Extends the 15.5-001 settings loader with a `theme` section; the render path injects `--accent` (and a computed `--accent-contrast`) into the token block. Contrast warning is a pure luminance computation — no new dependency.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 16.1-001, 15.2-001, 15.5-001
**Risk Level**: Low

## Epic Progress
**Completed**: 0 / 6 stories · 0 / 22 points
