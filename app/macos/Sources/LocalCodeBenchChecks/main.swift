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

    var failedStaysFailed = StartupTracker(timeout: 1)
    failedStaysFailed.begin()
    failedStaysFailed.pollFailed(elapsed: 2)
    failedStaysFailed.pollSucceeded()
    if case .failed = failedStaysFailed.state {
        expect(true, "successful poll after failure does not resurrect")
    } else {
        expect(false, "successful poll after failure does not resurrect")
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

    expectEqual(
        defaultAppSupportDirectory().lastPathComponent, "LocalCodeBench",
        "default app-support directory is named after the app")
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
    expectEqual(decide("http://127.0.0.1/x"), .openExternally, "portless http defaults to 80, not the dashboard port")

    let base80 = URL(string: "http://example.com/")!
    expectEqual(
        NavigationPolicy.decide(url: URL(string: "http://example.com:80/x")!, dashboardBaseURL: base80),
        .allow, "explicit :80 matches portless http base")

    let base443 = URL(string: "https://example.com/")!
    expectEqual(
        NavigationPolicy.decide(url: URL(string: "https://example.com:443/x")!, dashboardBaseURL: base443),
        .allow, "explicit :443 matches portless https base")

    let custom = URL(string: "bench://example.com/")!
    expectEqual(
        NavigationPolicy.decide(url: URL(string: "bench://example.com/x")!, dashboardBaseURL: custom),
        .allow, "portless non-http scheme matches itself")
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

// MARK: - ServiceController

/// Asks the kernel for a free localhost port so controller checks don't
/// collide with a real dashboard or with each other.
@MainActor func freePort() -> Int {
    let sock = socket(AF_INET, SOCK_STREAM, 0)
    defer { close(sock) }
    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = 0
    addr.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
    var len = socklen_t(MemoryLayout<sockaddr_in>.size)
    _ = withUnsafeMutablePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(sock, $0, len) }
    }
    _ = withUnsafeMutablePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(sock, $0, &len) }
    }
    return Int(UInt16(bigEndian: addr.sin_port))
}

@MainActor func waitFor(
    timeout: TimeInterval = 20, _ condition: () -> Bool
) async -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        if condition() { return true }
        try? await Task.sleep(for: .milliseconds(100))
    }
    return condition()
}

/// A service process that prints and dies immediately: the controller must
/// surface the exit code and expose the captured log for the failure view.
@MainActor func checkServiceControllerProcessDeath() async throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }

    let port = freePort()
    let plan = ServiceLaunchPlan(
        executable: "/bin/sh",
        arguments: ["-c", "echo boom; exit 7"],
        workingDirectory: dir,
        logFile: dir.appendingPathComponent("logs/service.log"))
    let controller = ServiceController(plan: plan, host: "127.0.0.1", port: port, timeout: 15)

    expectEqual(
        controller.baseURL.absoluteString, "http://127.0.0.1:\(port)/",
        "controller derives base url from host/port")
    expect(
        controller.healthURL.path.hasSuffix("api/status"),
        "controller polls the dashboard status endpoint")
    expectEqual(controller.logFile, plan.logFile, "controller exposes the plan's log file")

    controller.start()
    controller.start() // second call while startup is in flight is a no-op
    let failed = await waitFor {
        if case .failed = controller.state { return true }
        return false
    }
    expect(failed, "dead service process -> failed state")
    if case let .failed(reason) = controller.state {
        expect(reason.contains("7"), "failure reason carries the exit code")
    }
    expect(controller.logTail().contains("boom"), "failure view can tail the captured log")

    // Retry resets the tracker and relaunches; the same doomed plan fails again.
    controller.retry()
    let failedAgain = await waitFor {
        if case .failed = controller.state { return true }
        return false
    }
    expect(failedAgain, "retry relaunches and reaches a settled state")
    controller.shutdown()
}

/// A stub HTTP service (python http.server over a dir containing api/status)
/// stands in for `bench dashboard`: the controller must reach .ready, and a
/// second controller on the same port must reuse the running service instead
/// of launching its own.
@MainActor func checkServiceControllerReadyAndReuse() async throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }
    try FileManager.default.createDirectory(
        at: dir.appendingPathComponent("api"), withIntermediateDirectories: true)
    try "ok".write(
        to: dir.appendingPathComponent("api/status"), atomically: true, encoding: .utf8)

    let port = freePort()
    let plan = ServiceLaunchPlan(
        executable: "/usr/bin/env",
        arguments: ["python3", "-m", "http.server", String(port), "--bind", "127.0.0.1"],
        workingDirectory: dir,
        logFile: dir.appendingPathComponent("service.log"))
    let controller = ServiceController(plan: plan, host: "127.0.0.1", port: port, timeout: 15)

    controller.start()
    let ready = await waitFor {
        if case .ready = controller.state { return true }
        return false
    }
    expect(ready, "healthy service -> ready state")

    // Closing and reopening the window maps to a fresh controller pointed at
    // the same port: it must attach to the running service, not relaunch.
    let doomedIfLaunched = ServiceLaunchPlan(
        executable: "/nonexistent-local-code-bench",
        arguments: [],
        workingDirectory: dir,
        logFile: dir.appendingPathComponent("reuse.log"))
    let reattached = ServiceController(
        plan: doomedIfLaunched, host: "127.0.0.1", port: port, timeout: 15)
    reattached.start()
    let reused = await waitFor {
        if case .ready = reattached.state { return true }
        return false
    }
    expect(reused, "already-running service is reused, not relaunched")

    reattached.shutdown() // never launched a process: shutdown is a no-op
    controller.shutdown() // terminates the stub service
}

/// A plan whose executable does not exist: Process.run() throws and the
/// controller must fail with a launch error, never hang in .starting.
@MainActor func checkServiceControllerLaunchError() async throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }

    let plan = ServiceLaunchPlan(
        executable: "/nonexistent-local-code-bench",
        arguments: [],
        workingDirectory: dir,
        logFile: dir.appendingPathComponent("service.log"))
    let controller = ServiceController(plan: plan, host: "127.0.0.1", port: freePort(), timeout: 15)

    controller.start()
    let failed = await waitFor {
        if case .failed = controller.state { return true }
        return false
    }
    expect(failed, "unlaunchable executable -> failed state")
    // The immediate state carries "Could not launch…"; once the poll loop
    // settles the tracker's synthetic exit(-1) message wins. Either way the
    // reason must name a launch/exit problem, not be blank.
    if case let .failed(reason) = controller.state {
        expect(
            reason.contains("Could not launch") || reason.contains("-1"),
            "launch error reason names the cause")
    }
    controller.shutdown()
}

// MARK: - Runner

do {
    checkStartupTracker()
    try checkLogTail()
    try checkDataLocation()
    try checkCheckoutValidation()
    checkNavigationPolicy()
    try checkServiceLaunchPlan()
    try await checkServiceControllerProcessDeath()
    try await checkServiceControllerReadyAndReuse()
    try await checkServiceControllerLaunchError()
} catch {
    failures += 1
    print("FAIL: unexpected error thrown: \(error)")
}

print("\(passes) passed, \(failures) failed")
exit(failures == 0 ? 0 : 1)
