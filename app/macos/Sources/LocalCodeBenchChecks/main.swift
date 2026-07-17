// Assertion-based test runner for LocalCodeBenchKit (Story 18.1-001).
//
// `swift test` requires the XCTest / Testing runtime shipped only with full
// Xcode; the benchmark machine has Command Line Tools only. This executable is
// the kit's test suite instead: plain assertions, non-zero exit on failure.
// Run with: swift run LocalCodeBenchChecks
import Foundation
import LocalCodeBenchKit

var failures = 0
var passes = 0

@MainActor func expect(
    _ condition: @autoclosure () -> Bool,
    _ name: String,
    file: StaticString = #filePath,
    line: UInt = #line
) {
    if condition() {
        passes += 1
    } else {
        failures += 1
        print("FAIL: \(name) (\(file):\(line))")
    }
}

@MainActor func expectEqual<T: Equatable>(
    _ actual: T,
    _ expected: T,
    _ name: String,
    file: StaticString = #filePath,
    line: UInt = #line
) {
    if actual == expected {
        passes += 1
    } else {
        failures += 1
        print("FAIL: \(name) — got \(actual), expected \(expected) (\(file):\(line))")
    }
}

@MainActor func makeTempDir() throws -> URL {
    let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("lcb-checks-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

// MARK: - StartupTracker

@MainActor func checkStartupTracker() {
    var tracker = StartupTracker(timeout: 60)
    expectEqual(tracker.state, .idle, "tracker starts idle")

    tracker.begin()
    expectEqual(tracker.state, .starting(elapsed: 0), "begin -> starting(0)")

    tracker.pollFailed(elapsed: 5)
    expectEqual(tracker.state, .starting(elapsed: 5), "failed poll before timeout stays starting")

    tracker.pollSucceeded()
    expectEqual(tracker.state, .ready, "successful poll -> ready")

    tracker.pollFailed(elapsed: 120)
    expectEqual(tracker.state, .ready, "failed poll after ready does not flap back")

    var timedOut = StartupTracker(timeout: 60)
    timedOut.begin()
    timedOut.pollFailed(elapsed: 60)
    if case .failed = timedOut.state {
        expect(true, "poll failure at timeout -> failed")
    } else {
        expect(false, "poll failure at timeout -> failed")
    }

    var exited = StartupTracker(timeout: 60)
    exited.begin()
    exited.processExited(code: 3)
    if case let .failed(reason) = exited.state {
        expect(reason.contains("3"), "process exit during startup -> failed with exit code")
    } else {
        expect(false, "process exit during startup -> failed")
    }

    var diedAfterReady = StartupTracker(timeout: 60)
    diedAfterReady.begin()
    diedAfterReady.pollSucceeded()
    diedAfterReady.processExited(code: 1)
    if case .failed = diedAfterReady.state {
        expect(true, "process exit after ready -> failed")
    } else {
        expect(false, "process exit after ready -> failed")
    }
}

// MARK: - LogTail

@MainActor func checkLogTail() throws {
    expectEqual(LogTail.tail("a\nb\nc", lines: 2), "b\nc", "tail keeps last N lines")
    expectEqual(LogTail.tail("a\nb\nc", lines: 5), "a\nb\nc", "tail with short input keeps all")
    expectEqual(LogTail.tail("a\nb\nc\n", lines: 2), "b\nc", "tail ignores trailing newline")
    expectEqual(LogTail.tail("", lines: 3), "", "tail of empty text is empty")

    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }

    let log = dir.appendingPathComponent("service.log")
    try (1...100).map { "line \($0)" }.joined(separator: "\n")
        .write(to: log, atomically: true, encoding: .utf8)
    let tailed = LogTail.tail(fileAt: log, lines: 3)
    expectEqual(tailed, "line 98\nline 99\nline 100", "file tail keeps last N lines")

    let missing = dir.appendingPathComponent("nope.log")
    expectEqual(LogTail.tail(fileAt: missing, lines: 3), "", "missing log file tails to empty")

    // Only the final maxBytes of a large file are read, so tailing stays cheap.
    let big = dir.appendingPathComponent("big.log")
    let filler = String(repeating: "x", count: 1000)
    try (1...200).map { "\(filler) \($0)" }.joined(separator: "\n")
        .write(to: big, atomically: true, encoding: .utf8)
    let capped = LogTail.tail(fileAt: big, lines: 2, maxBytes: 4096)
    expect(capped.hasSuffix("199\n\(filler) 200"), "capped file tail still ends with last line")
}

// MARK: - DataLocation

@MainActor func checkDataLocation() throws {
    let suite = "lcb-checks-\(UUID().uuidString)"
    guard let defaults = UserDefaults(suiteName: suite) else {
        expect(false, "UserDefaults suite creation")
        return
    }
    defer { defaults.removePersistentDomain(forName: suite) }

    let store = DataLocationStore(defaults: defaults)
    expect(store.isFirstRun, "empty defaults -> first run")
    expectEqual(store.recorded, nil, "empty defaults -> no recorded location")

    store.record(.appSupportDefault)
    expect(!store.isFirstRun, "recorded choice ends first run")
    expectEqual(store.recorded, .appSupportDefault, "app-support choice round-trips")

    let checkout = URL(fileURLWithPath: "/Users/fx/dev/local-code-bench")
    store.record(.checkout(checkout))
    expectEqual(store.recorded, .checkout(checkout), "checkout choice round-trips")

    defaults.set("garbage".data(using: .utf8), forKey: DataLocationStore.key)
    expectEqual(store.recorded, nil, "corrupt stored value falls back to first run")
}

@MainActor func checkCheckoutValidation() throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }

    expect(!CheckoutValidation.isBenchCheckout(dir), "empty dir is not a checkout")

    try FileManager.default.createDirectory(
        at: dir.appendingPathComponent("configs"), withIntermediateDirectories: true)
    expect(!CheckoutValidation.isBenchCheckout(dir), "configs/ alone is not a checkout")

    try "[project]\nname = \"local-code-bench\"\n"
        .write(to: dir.appendingPathComponent("pyproject.toml"), atomically: true, encoding: .utf8)
    expect(CheckoutValidation.isBenchCheckout(dir), "configs/ + pyproject.toml is a checkout")
}

// MARK: - NavigationPolicy

@MainActor func checkNavigationPolicy() {
    let base = URL(string: "http://127.0.0.1:8765/")!

    func decide(_ url: String) -> NavigationDecision {
        NavigationPolicy.decide(url: URL(string: url)!, dashboardBaseURL: base)
    }

    expectEqual(decide("http://127.0.0.1:8765/api/data"), .allow, "same host+port stays in app")
    expectEqual(decide("http://127.0.0.1:8765"), .allow, "base url itself stays in app")
    expectEqual(decide("about:blank"), .allow, "about:blank stays in app")
    expectEqual(decide("http://127.0.0.1:9999/"), .openExternally, "other port opens in browser")
    expectEqual(decide("https://github.com/fxmartin"), .openExternally, "external site opens in browser")
    expectEqual(decide("mailto:mail@fxmartin.me"), .openExternally, "mailto opens externally")
}

// MARK: - ServiceLaunchPlan

@MainActor func checkServiceLaunchPlan() throws {
    let checkout = URL(fileURLWithPath: "/Users/fx/dev/local-code-bench")
    let plan = ServiceLaunchPlan.plan(
        for: .checkout(checkout), host: "127.0.0.1", port: 8765)
    expectEqual(plan.executable, "/usr/bin/env", "checkout plan runs through env")
    expectEqual(
        plan.arguments,
        ["uv", "run", "bench", "dashboard", "--host", "127.0.0.1", "--port", "8765"],
        "checkout plan uses uv run inside the checkout")
    expectEqual(plan.workingDirectory, checkout, "checkout plan cwd is the checkout")
    expect(
        plan.logFile.path.hasSuffix("dashboard-service.log"),
        "checkout plan logs to dashboard-service.log")

    let appSupport = URL(fileURLWithPath: "/tmp/lcb-app-support")
    let defaultPlan = ServiceLaunchPlan.plan(
        for: .appSupportDefault, host: "127.0.0.1", port: 8765, appSupportDirectory: appSupport)
    expectEqual(
        defaultPlan.arguments,
        ["bench", "dashboard", "--host", "127.0.0.1", "--port", "8765"],
        "app-support plan calls the installed bench CLI directly")
    expectEqual(defaultPlan.workingDirectory, appSupport, "app-support plan cwd is app support")
}

// MARK: - Runner

do {
    checkStartupTracker()
    try checkLogTail()
    try checkDataLocation()
    try checkCheckoutValidation()
    checkNavigationPolicy()
    try checkServiceLaunchPlan()
} catch {
    failures += 1
    print("FAIL: unexpected error thrown: \(error)")
}

print("\(passes) passed, \(failures) failed")
exit(failures == 0 ? 0 : 1)
