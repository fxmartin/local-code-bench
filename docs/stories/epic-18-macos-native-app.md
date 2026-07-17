# Epic 18: macOS Native App Shell

## Epic Overview
**Epic ID**: Epic-18
**Description**: Ship the harness as a real, double-clickable macOS app — **Option A from the 2026-07-17 analysis**: a SwiftUI shell that supervises a bundled Python harness as a background service and hosts the existing unified dashboard (Epic-09, restyled by Epic-16) in a `WKWebView`, adding what a browser tab cannot: a Dock and menu-bar presence with live status (running inferencer, benchmark progress, tier moves), native notifications on run completion and failures, process lifecycle ownership (launch, health-check, restart, clean shutdown), and a signed + notarized Developer ID distribution anyone with an Apple Silicon Mac can download and run. The web dashboard remains the single UI — every current and planned surface (Settings tab, Benchmarks tab) lands inside the app for free — and the ~15.6k lines of tested Python remain the single implementation of all harness logic, consumed over the same localhost JSON API the browser uses today.
**Business Value**: Today running the harness means a terminal, `uv`, and a browser tab that dies with the shell. An app makes the benchmark rig a persistent, glanceable part of the Mac: a suite run visible from the menu bar while FX works elsewhere, a notification when 21 models finish (or when one wedges), no orphaned servers, and a distribution story — "download the app" instead of "clone the repo and install uv" — for anyone else who wants to reproduce the numbers on their own M-series machine. It also future-proofs: because the shell consumes the public `/api/*` seam, a later native SwiftUI client (Option B) is an addition, not a migration.
**Success Metrics**: FX can install a signed, notarized `.app` on a clean Mac with no Python/uv/dev tooling and be looking at the dashboard in under a minute; quitting the app never leaves an orphaned harness process, and killing the harness never leaves a dead app window (it restarts and reattaches); the menu-bar extra shows the running engine and live run/move progress within one polling interval; a finished or failed suite run raises a native notification; the app coexists correctly with a CLI-started dashboard (attach, don't fight over the port); and the packaged app passes Gatekeeper on first launch with no right-click-open ritual.

## Epic Scope
**Total Stories**: 6 | **Total Points**: 26 | **MVP Stories**: 0 (Should Have / v2)

## Decisions Locked With FX
- **Option A** (native shell over the web dashboard), per the analysis: the web UI stays the single UI; Option B (native SwiftUI views) is explicitly deferred and remains possible later because it would consume the same API.
- **No Mac App Store.** The harness spawns inference servers and executes untrusted generated code — fundamentally incompatible with Apple's App Sandbox. Distribution is **Developer ID signing + notarization**, direct download.
- **Detect-only externals preserved.** The app bundles CPython + the harness wheel and nothing else; Ollama, mlx-lm, and Codex are detected and linked, never installed — same philosophy as the inferencer registry.

## Decisions Locked With FX (confirmed 2026-07-17)
- **Apple Developer account: none for now — local-only.** The app is **ad-hoc signed** for local use; story 18.3-002's notarized-distribution AC is **deferred** (not built) until a Developer ID exists. Everything else in 18.3-002 (signing/packaging pipeline) still completes.
- **App identity**: name **"Local Code Bench"**, bundle id `me.fxmartin.local-code-bench`.
- **Shape**: a regular **windowed app plus a menu-bar extra** — the window is closable while benchmark runs continue in the background. (Menu-bar-only was rejected.)
- **Minimum macOS**: **macOS 14 (Sonoma)** — matches the M3 Max fleet and keeps SwiftUI menu-bar APIs simple.
- **Auto-update**: **deferred from v1** (manual download of new versions); adopting Sparkle later is compatible with the packaging pipeline.

## Scope Boundaries (explicitly NOT building)
- **No native re-implementation of dashboard views** — no SwiftUI tables/charts duplicating the web UI (that is Option B, a separate future epic if ever).
- **No Mac App Store build**, no App Sandbox entitlement work beyond what notarization's hardened runtime requires.
- **No bundled inference engines or agent CLIs** — the app inherits the harness's detect-and-link behavior unchanged.
- **No auto-update framework in v1** — version checks against GitHub Releases may show an "update available" hint at most; installing updates is manual.
- **No cross-platform shell** (Tauri/Electron) — this is an Apple-Silicon-specific benchmark; the shell is Swift.

## Design Reference
- **Architecture**: `LocalCodeBench.app` (SwiftUI) ⇄ localhost HTTP ⇄ bundled `bench dashboard` service (embedded relocatable CPython + the project wheel + its two runtime deps). The Swift layer contains zero benchmark logic: it supervises the process, renders the WKWebView, polls a handful of read-only `/api/*` endpoints for status, and posts notifications. All state lives where it lives today (`configs/`, `results/`, `.runtime/`), resolved to standard app locations (`~/Library/Application Support/LocalCodeBench` by default, configurable — nothing-hardcoded — with a first-run option to point at an existing repo checkout so CLI and app share configs/results).
- **Port & coexistence**: the app asks the service to bind an ephemeral or configured port and reads it from a ready-file; on launch it first probes for an already-running dashboard (CLI-started) and attaches instead of spawning a second instance, with the status UI showing which mode it is in.
- **Status without new endpoints**: the menu-bar extra polls existing seams — `/api/status` (inferencers), `/api/runs` (live benchmark progress), `/api/move-status` (tier moves) — so Epic-18 adds no Python surface area beyond, at most, a version/ready endpoint.
- **Signing**: hardened runtime with the entitlements an embedded interpreter needs (`disable-library-validation`, `allow-unsigned-executable-memory` if required by CPython), every bundled dylib/so codesigned; the benchmark sandbox (subprocess + temp dir) is unaffected because App Sandbox is not in play.
- **Version alignment**: the app version mirrors `pyproject.toml:project.version` (the PSR-managed number) so an app build is traceable to a harness release.

## Features in This Epic

### Feature 18.1: App Shell & Service Supervision

#### Stories

##### Story 18.1-001: SwiftUI shell hosting the dashboard
**User Story**: As FX, I want a macOS app window that hosts the unified dashboard, so that the harness feels like a Mac application — Dock icon, real window, no browser tab to lose.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the app launches and the service is ready **When** the window opens **Then** it renders the dashboard in a `WKWebView` full-bleed (no browser chrome), with window size/position restored across launches.
- **Given** the service is still starting **When** the window opens **Then** a native loading state shows startup progress and tails the service log on failure — never a white WKWebView error page.
- **Given** the window is closed **When** runs or moves are in progress **Then** the app keeps running (menu-bar/Dock) and reopening the window reattaches to the same session state.
- **Given** first run **When** the app starts with no prior data **Then** a minimal first-run panel offers the default app-support data location or picking an existing `local-code-bench` checkout (shared configs/results with the CLI), and records the choice.

**Technical Notes**: One SwiftUI `WindowGroup` + `WKWebView` wrapper; JS↔Swift bridging kept to nearly nothing (external links open in the default browser; downloads — the Epic-17 PDF — save via the standard panel). Xcode project lives under `app/macos/` in-repo.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 09.1-001
**Risk Level**: Medium

##### Story 18.1-002: Bundled harness as a supervised service
**User Story**: As FX, I want the app to own the harness process — start, health-check, restart, stop — with a relocatable Python bundled inside the app, so that the app works on a machine with no Python tooling and never leaves orphans.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a clean Mac without Python/uv **When** the app launches **Then** the embedded CPython + harness wheel start `bench dashboard` headless on an app-chosen port, and readiness is detected via health probe within a bounded timeout.
- **Given** the service crashes **When** the app is running **Then** it is restarted with backoff, the UI shows the interruption, and repeated crash-looping surfaces the log instead of restarting forever.
- **Given** the app quits (or is force-quit and relaunched) **When** shutdown/startup runs **Then** no orphaned harness or inferencer-management processes remain, and a stale ready-file/port from a previous crash is detected and cleaned.
- **Given** a dashboard already running from the CLI **When** the app launches **Then** it attaches to it instead of spawning a second service, labels the mode in the status UI, and quitting the app leaves the CLI-owned process untouched.

**Technical Notes**: Embed a relocatable CPython (python-build-standalone) + the wheel + `pyyaml`/`python-dotenv` in `Contents/Resources`; supervision via `Process` with a process-group kill on quit. Reuse the dashboard's existing lifecycle/state-file conventions (`dashboard_lifecycle`) for the ready/port handshake rather than inventing a second one.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 18.1-001
**Risk Level**: High

### Feature 18.2: Native Presence

#### Stories

##### Story 18.2-001: Menu-bar status and native notifications
**User Story**: As FX, I want a menu-bar extra showing what the rig is doing — running engine, live benchmark progress, tier moves — and native notifications when long work finishes or fails, so that I can glance instead of switching to the dashboard.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** the app is running **When** I open the menu-bar extra **Then** it shows the active inferencer + model (from `/api/status`), any live run with suite/model progress (from `/api/runs`), and any tier move with its live byte progress (from `/api/move-status`), refreshed on a polling interval.
- **Given** a suite run completes or aborts **When** the app is in the background **Then** a native notification reports the outcome (models completed, failures), and clicking it opens the window on the relevant section.
- **Given** a tier move or auto-tier apply finishes or errors **When** in the background **Then** the same notification path fires with the move verdict.
- **Given** the harness service is down (crash-looping, CLI instance gone) **When** the menu-bar renders **Then** the icon reflects the degraded state and offers restart — status is never silently stale.

**Technical Notes**: `MenuBarExtra` (SwiftUI) + `UNUserNotificationCenter`; a single poller feeding both menu and notification triggers, with edge-detection (state transitions fire notifications, not polls). Poll interval is a setting per nothing-hardcoded.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 18.1-002
**Risk Level**: Medium

##### Story 18.2-002: macOS conveniences
**User Story**: As FX, I want the small native touches — open results/reports in Finder, recent reports in the Dock menu, optional launch-at-login — so that the app behaves like it belongs on the platform.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** the Dock or menu-bar menu **When** opened **Then** it offers "Open Results Folder" and the most recent Epic-17 PDF reports, revealing them in Finder.
- **Given** the app's settings **When** launch-at-login is enabled **Then** the app registers as a login item via the modern `SMAppService` API and starts quietly in the menu bar.
- **Given** an Epic-17 PDF download triggered inside the WKWebView **When** it completes **Then** it lands in the user-visible reports location and a notification offers "Reveal in Finder".

**Technical Notes**: Thin story: `NSWorkspace` reveals, `SMAppService`, a small recents list read from `results/reports/`. No harness changes.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 18.2-001
**Risk Level**: Low

### Feature 18.3: Packaging, Signing & Distribution

#### Stories

##### Story 18.3-001: Reproducible signed build pipeline
**User Story**: As FX, I want one command that produces a signed `.app` with the embedded Python correctly codesigned, so that building the app is as repeatable as building the wheel.
**Priority**: Should Have
**Story Points**: 5

**Acceptance Criteria**:
- **Given** a checkout on a Mac with Xcode and the Developer ID certificate **When** the build script runs **Then** it assembles the app (Swift build + relocatable CPython + freshly built wheel), codesigns every bundled binary/dylib inside-out with hardened runtime and the required entitlements, and emits a verifiable `.app` (`codesign --verify --deep` clean).
- **Given** `pyproject.toml`'s version **When** the app builds **Then** `CFBundleShortVersionString` mirrors it and the about panel shows both app and harness versions.
- **Given** no Developer ID certificate **When** the build runs **Then** it falls back to ad-hoc signing for local use, clearly labeled unsigned-for-distribution.
- **Given** the build script **When** inspected **Then** every tool/version/path it depends on is declared in config, not hardcoded (Epic-15 principle applies to the build too).

**Technical Notes**: Prefer a plain scripted pipeline (`scripts/build-app.sh` + `xcodebuild`) over Briefcase so the Swift shell stays a first-class Xcode project; python-build-standalone pinned by version + checksum. CI note: GitHub Actions macOS runners can build unsigned; signing/notarization need secrets — treat CI signing as out of scope for this story, local build is the deliverable.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 18.1-002
**Risk Level**: High

##### Story 18.3-002: Notarization and distribution
**User Story**: As FX, I want the built app notarized, stapled, and packaged for download, so that a fresh Mac opens it with no Gatekeeper friction and others can reproduce my benchmarks from a download.
**Priority**: Should Have
**Story Points**: 3

**Acceptance Criteria**:
- **Given** a signed build **When** the distribution step runs **Then** it submits via `notarytool`, staples the ticket, and produces a distributable DMG (or zip) that passes `spctl --assess` on a clean machine with a normal double-click open.
- **Given** a published app version **When** compared to the repo **Then** it corresponds to a tagged harness release (PSR version), and the release notes state the bundled harness version and detected-not-bundled externals.
- **Given** the README/docs **When** updated **Then** they document both install paths — download the app, or `uv run bench dashboard` from a checkout — and what differs (nothing, functionally).
- **Given** a newer GitHub Release **When** the app checks (best-effort, on launch, respecting offline) **Then** an unobtrusive "update available" hint links to the download; no auto-install.

**Technical Notes**: `xcrun notarytool` with a keychain profile; the update hint reuses the existing `gh`-less environment convention (plain HTTPS GET to the releases API, silent on failure). Blocked on the Developer-account decision above.

**Definition of Done**:
- [ ] Code implemented and peer reviewed
- [ ] Tests written and passing
- [ ] Documentation updated

**Dependencies**: 18.3-001
**Risk Level**: Medium

## Epic Progress
**Completed**: 0 / 6 stories · 0 / 26 points
