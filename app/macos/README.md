# Local Code Bench — macOS App Shell

A native SwiftUI shell (Story 18.1-001) that hosts the unified dashboard
(`bench dashboard`, Epic-09) in a full-bleed `WKWebView`, so the harness feels
like a Mac application: Dock icon, a real window with its size/position
restored across launches, and no browser tab to lose.

## What it does

- **Owns the service.** On launch it starts `bench dashboard` on
  `127.0.0.1:8765` (or reuses one that is already answering) and polls
  `GET /api/status` until it is ready.
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
- **Minimal bridging.** Same-origin navigations render in the web view;
  external links open in the default browser; downloads (e.g. the Epic-17
  comparison PDF) go through the standard save panel. There is no JS↔Swift
  message channel.

## Layout

| Target | Role |
|--------|------|
| `LocalCodeBenchKit` | Pure-Foundation logic: `StartupTracker` (startup state machine), `LogTail`, `DataLocationStore` / `CheckoutValidation`, `NavigationPolicy`, `ServiceLaunchPlan`, and the `ServiceController` process/polling glue. |
| `LocalCodeBench` | The SwiftUI app: `WindowGroup` + `MenuBarExtra`, `WKWebView` wrapper, loading/failure/first-run views, window-frame autosave. |
| `LocalCodeBenchChecks` | The kit's test suite as an assertion-based executable. |

## Build, run, test

The package builds with Command Line Tools alone — full Xcode is only needed
if you want to open `Package.swift` in Xcode or produce a signed `.app` bundle.

```bash
swift build                        # compile everything
swift run LocalCodeBench           # run the shell (unbundled, for development)
swift run LocalCodeBenchChecks     # run the test suite (exit code 0 = green)
```

`swift test` is deliberately not used: the XCTest / Swift Testing runtime ships
only with full Xcode, and the benchmark machine has Command Line Tools only.
`LocalCodeBenchChecks` covers the same ground with plain assertions and a
non-zero exit on failure.

## Service launch modes

The launch plan depends on the recorded data location:

- **Checkout**: `uv run bench dashboard --host 127.0.0.1 --port 8765` with the
  checkout as cwd, so the service uses that checkout's environment, configs,
  and results — identical to running the CLI there.
- **App-support default**: `bench dashboard …` from `PATH` (e.g. after
  `uv tool install`) with the app-support directory as cwd; `configs/` and
  `results/` are created there on first run.

Service stdout/stderr are captured to
`~/Library/Application Support/LocalCodeBench/dashboard-service.log`.
