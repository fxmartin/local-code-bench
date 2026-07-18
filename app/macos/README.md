# Local Code Bench — macOS App Shell

A native SwiftUI shell (Stories 18.1-001/18.1-002/18.2-001) that hosts the unified
dashboard (`bench dashboard`, Epic-09) in a full-bleed `WKWebView`, so the
harness feels like a Mac application: Dock icon, a real window with its
size/position restored across launches, and no browser tab to lose.

## What it does

- **Owns the service.** On launch it starts `bench dashboard` on
  `127.0.0.1:8765` (or reuses one that is already answering) and polls
  `GET /api/status` until it is ready.
- **Supervises it.** If the launched service crashes, it is restarted with
  exponential backoff (`RestartPolicy`; the menu bar shows the restart
  attempt); crash-looping past the limit gives up and surfaces the failure
  with the service log instead of restarting forever. A stale
  `.runtime/dashboard.json` left by a previous crash is removed (only when its
  recorded pid is provably dead) before launching.
- **Never leaves orphans.** Quitting terminates the service's whole process
  group, and every app-launched service runs with `--exit-with-parent` — a
  watchdog in the dashboard that terminates it the moment the app dies, so
  even a force-quit cleans up.
- **Attaches to a CLI-owned dashboard.** A dashboard already running from the
  CLI is reused instead of spawning a second service; the menu bar labels the
  mode (`app-managed` vs `CLI-owned`) and quitting the app leaves a CLI-owned
  process untouched (attached services are never supervised or stopped).
- **Native startup state.** While the service is starting, the window shows a
  native progress view; if startup fails (process exit or timeout) it shows the
  failure reason plus the tail of the captured service log with Retry / Open
  Log actions — never a white WKWebView error page.
- **Survives window close.** Closing the window leaves the app (menu bar +
  Dock) and the service process running, so in-flight runs and tier moves
  continue; reopening the window points a fresh web view at the same
  still-running service.
- **First-run choice.** With no recorded data location, a minimal panel offers
  the default `~/Library/Application Support/LocalCodeBench` directory or an
  existing `local-code-bench` checkout (validated: `configs/` +
  `pyproject.toml`), so configs/results are shared with the CLI. The choice is
  recorded in user defaults.
- **Menu-bar status.** The menu-bar extra shows what the rig is doing without
  opening the window: the running engine (from `/api/status`), each tracked
  run's live suite progress and pass/fail counts (from `/api/runs`), and any
  tier move with its live byte progress (from `/api/move-status`), all fed by
  one poller. When the service fails or a ready service stops answering (e.g.
  a CLI-owned dashboard whose process is gone), the icon flips to a warning
  triangle and the menu offers **Restart Service** — status is never silently
  stale. The poll interval is a setting, not a constant:
  `defaults write me.fxmartin.local-code-bench statusPollIntervalSeconds -float 5`
  (default 2 s, clamped to 0.5–60 s).
- **Native notifications.** When the app is in the background, a suite run
  completing or failing — and a tier move finishing or erroring — posts a
  native notification with the outcome (edge-detected from the same poller:
  state *transitions* fire, polls do not). Clicking it opens the window on the
  relevant dashboard section (Run / Inventory). Notifications need a real
  `.app` bundle; unbundled `swift run` dev builds skip them (menu-bar status
  still works).
- **Update hint.** On launch the app asks the GitHub releases API (repo
  stamped into Info.plist as `LCBGitHubRepo` at build time) whether a newer
  release exists — best-effort and silent on any failure, so offline launches
  see nothing. When one exists, the menu bar gains a single
  "Update Available — Download …" entry that opens the release page in the
  browser; there is no auto-install. Unbundled dev builds skip the check.
- **Minimal bridging.** Same-origin navigations render in the web view;
  external links open in the default browser; downloads (e.g. the Epic-17
  comparison PDF) go through the standard save panel. There is no JS↔Swift
  message channel.

## Layout

| Target | Role |
|--------|------|
| `LocalCodeBenchKit` | Pure-Foundation logic: `StartupTracker` (startup state machine), `RestartPolicy` (crash/backoff rules), `StaleServiceState`, `BundledRuntime`, `LogTail`, `DataLocationStore` / `CheckoutValidation`, `NavigationPolicy`, `ServiceLaunchPlan`, the `ServiceController` process/supervision glue, and the status pipeline (`RigSnapshot` parsing, `StatusEventDetector` edge detection, `MenuBarStatus` / `NotificationContent` formatting, `StatusPollSettings`, `StatusPoller`), and `UpdateCheck` (release-hint decision + best-effort fetch). |
| `LocalCodeBench` | The SwiftUI app: `WindowGroup` + `MenuBarExtra`, `WKWebView` wrapper, loading/failure/first-run views, window-frame autosave, `UNUserNotificationCenter` glue (`StatusNotifier`). |
| `LocalCodeBenchChecks` | The kit's test suite as an assertion-based executable. |

## Build, run, test

The package builds with Command Line Tools alone — full Xcode is only needed
if you want to open `Package.swift` in Xcode or produce a signed `.app` bundle.

```bash
swift build                        # compile everything
swift run LocalCodeBench           # run the shell (unbundled, for development)
swift run LocalCodeBenchChecks     # run the test suite (exit code 0 = green)
```

To produce a self-contained bundle that needs no Python/uv on the target Mac,
run `scripts/build-macos-app.sh` from the repo root: it builds the shell in
release mode, downloads a pinned, checksum-verified relocatable CPython
(python-build-standalone), installs the harness wheel (with `pyyaml` /
`python-dotenv`) into `Contents/Resources/python`, and assembles + signs
`dist/LocalCodeBench.app` (Story 18.3-001):

- Every bundled Mach-O (dylibs, extension modules, the embedded `python3.12`,
  the app executable) is signed inside-out with the hardened runtime and
  `app/macos/entitlements.plist`; the script finishes with a clean
  `codesign --verify --deep --strict`.
- With a "Developer ID Application" certificate in the keychain (or a
  `codesign_identity` pin) the bundle is distribution-signed; otherwise it
  falls back to ad-hoc signing, labeled unsigned-for-distribution.
- Every pin the script depends on — CPython version/tag/SHA-256, bundle id,
  minimum macOS, entitlements path, signing identity — lives in
  `configs/build.yaml`; same-named env vars (`PBS_TAG`, `CODESIGN_IDENTITY`,
  …) override it for one-off experiments, and `--print-config` prints the
  resolved values without building.
- `CFBundleShortVersionString` mirrors `pyproject.toml`'s version, and the
  About panel shows both the app version and the bundled harness version
  (read from `Contents/Resources/harness-version`).

## Distribution

`scripts/distribute-macos-app.sh` turns a Developer-ID-signed build into the
downloadable release artifact (Story 18.3-002):

1. Refuses ad-hoc-signed bundles (they are labeled unsigned-for-distribution
   by the build script, and Apple would reject them anyway).
2. Enforces release alignment: the bundle's `CFBundleShortVersionString`, the
   bundled harness wheel, and `pyproject.toml`'s PSR-managed version must all
   agree, so every published DMG corresponds to a tagged harness release
   (`vX.Y.Z`).
3. Submits the app to Apple with `xcrun notarytool submit --wait` using the
   `notary_profile` keychain profile from `configs/build.yaml` (create it once
   with `xcrun notarytool store-credentials`), gating on `status: Accepted`.
4. Staples the ticket, wraps the app in a drag-install DMG
   (`dist/LocalCodeBench-<version>.dmg`, with an `/Applications` symlink),
   then notarizes and staples the DMG too.
5. Verifies both with Gatekeeper itself: `spctl --assess --type execute` on
   the app and `spctl --assess --type open` on the DMG — so a clean machine
   opens the download with a normal double-click.
6. Writes `dist/RELEASE-NOTES-<version>.md` stating the bundled harness and
   CPython versions and the detected-not-bundled externals (inference
   engines, agent CLIs, proxies, `uv`).

`NOTARY_PROFILE` / `GITHUB_REPO` env vars override the config pins, and
`--print-config` prints the resolved values without touching Apple's servers.

`swift test` is deliberately not used: the XCTest / Swift Testing runtime ships
only with full Xcode, and the benchmark machine has Command Line Tools only.
`LocalCodeBenchChecks` covers the same ground with plain assertions and a
non-zero exit on failure.

## Service launch modes

The launch plan depends on the bundled runtime and the recorded data location
(every app-launched variant appends `--exit-with-parent`):

- **Bundled** (a built `.app`): `Contents/Resources/python/bin/python3 -m
  local_code_bench dashboard …` — the embedded interpreter launches the CLI as
  a module because console-script shims carry absolute build-time shebangs.
  The cwd still follows the recorded data location.
- **Checkout** (dev builds): `uv run bench dashboard --host 127.0.0.1 --port
  8765 …` with the checkout as cwd, so the service uses that checkout's
  environment, configs, and results — identical to running the CLI there.
- **App-support default** (dev builds): `bench dashboard …` from `PATH` (e.g.
  after `uv tool install`) with the app-support directory as cwd; `configs/`
  and `results/` are created there on first run.

Service stdout/stderr are captured to
`~/Library/Application Support/LocalCodeBench/dashboard-service.log`.
