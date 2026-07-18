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
    // Every app-launched service gets --exit-with-parent, so a force-quit of
    // the app can never leave an orphaned dashboard behind.
    let dashboardArgs = [
        "dashboard", "--host", "127.0.0.1", "--port", "8765", "--exit-with-parent",
    ]

    let checkout = URL(fileURLWithPath: "/Users/fx/dev/local-code-bench")
    let plan = ServiceLaunchPlan.plan(
        for: .checkout(checkout), host: "127.0.0.1", port: 8765)
    expectEqual(plan.executable, "/usr/bin/env", "checkout plan runs through env")
    expectEqual(
        plan.arguments,
        ["uv", "run", "bench"] + dashboardArgs,
        "checkout plan uses uv run inside the checkout")
    expectEqual(plan.workingDirectory, checkout, "checkout plan cwd is the checkout")
    expect(
        plan.logFile.path.hasSuffix("dashboard-service.log"),
        "checkout plan logs to dashboard-service.log")
    expectEqual(
        plan.stateFile, checkout.appendingPathComponent(".runtime/dashboard.json"),
        "plan resolves the dashboard_lifecycle state file against its cwd")

    let appSupport = URL(fileURLWithPath: "/tmp/lcb-app-support")
    let defaultPlan = ServiceLaunchPlan.plan(
        for: .appSupportDefault, host: "127.0.0.1", port: 8765, appSupportDirectory: appSupport)
    expectEqual(
        defaultPlan.arguments,
        ["bench"] + dashboardArgs,
        "app-support plan calls the installed bench CLI directly")
    expectEqual(defaultPlan.workingDirectory, appSupport, "app-support plan cwd is app support")

    // With a bundled runtime the CLI is launched as a module: console-script
    // shims carry absolute build-time shebangs, `-m` works from anywhere.
    let runtime = BundledRuntime(
        python: URL(fileURLWithPath: "/Applications/LCB.app/Contents/Resources/python/bin/python3"))
    let bundledPlan = ServiceLaunchPlan.plan(
        for: .checkout(checkout), host: "127.0.0.1", port: 8765,
        runtime: runtime, appSupportDirectory: appSupport)
    expectEqual(
        bundledPlan.executable, runtime.python.path,
        "bundled plan runs the embedded interpreter")
    expectEqual(
        bundledPlan.arguments,
        ["-m", "local_code_bench"] + dashboardArgs,
        "bundled plan launches the CLI as a module")
    expectEqual(
        bundledPlan.workingDirectory, checkout,
        "bundled plan keeps the data location's cwd")
}

// MARK: - BundledRuntime

@MainActor func checkBundledRuntime() throws {
    expectEqual(
        BundledRuntime.locate(resourcesDirectory: nil), nil,
        "no resources directory (dev build) -> no bundled runtime")

    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }

    expectEqual(
        BundledRuntime.locate(resourcesDirectory: dir), nil,
        "resources without python/bin/python3 -> no bundled runtime")

    let bin = dir.appendingPathComponent("python/bin")
    try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
    let python = bin.appendingPathComponent("python3")
    try "#!/bin/sh\n".write(to: python, atomically: true, encoding: .utf8)

    expectEqual(
        BundledRuntime.locate(resourcesDirectory: dir), nil,
        "non-executable python3 -> no bundled runtime")

    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: python.path)
    expectEqual(
        BundledRuntime.locate(resourcesDirectory: dir), BundledRuntime(python: python),
        "executable Resources/python/bin/python3 is located")
}

// MARK: - AboutInfo

@MainActor func checkAboutInfo() throws {
    expectEqual(
        AboutInfo.resolve(bundleShortVersion: nil, bundledHarnessVersion: nil),
        AboutInfo(appVersion: "dev", harnessVersion: "unbundled"),
        "dev build (no bundle, no runtime) -> dev/unbundled")

    expectEqual(
        AboutInfo.resolve(bundleShortVersion: "0.75.0", bundledHarnessVersion: "0.75.0"),
        AboutInfo(appVersion: "0.75.0", harnessVersion: "0.75.0"),
        "bundled build shows both versions")

    expectEqual(
        AboutInfo.resolve(bundleShortVersion: "  ", bundledHarnessVersion: ""),
        AboutInfo(appVersion: "dev", harnessVersion: "unbundled"),
        "blank versions fall back like missing ones")

    expectEqual(
        AboutInfo.bundledHarnessVersion(resourcesDirectory: nil), nil,
        "no resources directory -> no harness version")

    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }

    expectEqual(
        AboutInfo.bundledHarnessVersion(resourcesDirectory: dir), nil,
        "missing harness-version file -> nil")

    let file = dir.appendingPathComponent("harness-version")
    try "0.75.0\n".write(to: file, atomically: true, encoding: .utf8)
    expectEqual(
        AboutInfo.bundledHarnessVersion(resourcesDirectory: dir), "0.75.0",
        "harness-version file is read and trimmed")

    try "\n".write(to: file, atomically: true, encoding: .utf8)
    expectEqual(
        AboutInfo.bundledHarnessVersion(resourcesDirectory: dir), nil,
        "whitespace-only harness-version file -> nil")
}

// MARK: - UpdateCheck

@MainActor func checkUpdateCheck() async {
    // Version comparison: strictly-newer numeric tags hint, everything else
    // (equal, older, non-numeric, dev builds) stays silent.
    expect(
        UpdateCheck.isNewer(remoteTag: "v0.76.0", currentVersion: "0.75.0"),
        "newer minor tag is newer")
    expect(
        UpdateCheck.isNewer(remoteTag: "v0.75.1", currentVersion: "0.75.0"),
        "newer patch tag is newer")
    expect(
        UpdateCheck.isNewer(remoteTag: "v1.0", currentVersion: "0.75.0"),
        "shorter-but-larger tag is newer")
    expect(
        !UpdateCheck.isNewer(remoteTag: "v0.75.0", currentVersion: "0.75.0"),
        "equal versions do not hint")
    expect(
        !UpdateCheck.isNewer(remoteTag: "v0.75", currentVersion: "0.75.0"),
        "equal versions of different lengths do not hint")
    expect(
        !UpdateCheck.isNewer(remoteTag: "v0.74.9", currentVersion: "0.75.0"),
        "older tag does not hint")
    expect(
        !UpdateCheck.isNewer(remoteTag: "nightly", currentVersion: "0.75.0"),
        "non-numeric tag stays silent")
    expect(
        !UpdateCheck.isNewer(remoteTag: "v0.76.0", currentVersion: "dev"),
        "dev build stays silent")

    // Payload parsing: GitHub's releases/latest JSON, malformed data, and a
    // 404-style body must all be handled without error.
    let releaseJSON = #"""
    {"tag_name": "v0.76.0",
     "html_url": "https://github.com/fx/lcb/releases/tag/v0.76.0"}
    """#
    expectEqual(
        UpdateCheck.parseLatestRelease(Data(releaseJSON.utf8)),
        LatestRelease(
            tag: "v0.76.0",
            url: URL(string: "https://github.com/fx/lcb/releases/tag/v0.76.0")),
        "release payload parses tag and html_url")
    expectEqual(
        UpdateCheck.parseLatestRelease(Data("not json".utf8)), nil,
        "malformed payload parses to nil")
    expectEqual(
        UpdateCheck.parseLatestRelease(Data(#"{"message": "Not Found"}"#.utf8)), nil,
        "payload without tag_name parses to nil")

    // Hint decision: only a strictly newer release produces a hint; the link
    // prefers the payload's html_url, falling back to the releases page.
    expectEqual(
        UpdateCheck.hint(
            currentVersion: "0.75.0", repo: "fx/lcb",
            releaseData: Data(releaseJSON.utf8)),
        UpdateHint(
            version: "0.76.0",
            url: URL(string: "https://github.com/fx/lcb/releases/tag/v0.76.0")!),
        "newer release hints with stripped version and release url")
    expectEqual(
        UpdateCheck.hint(
            currentVersion: "0.75.0", repo: "fx/lcb",
            releaseData: Data(#"{"tag_name": "v0.76.0"}"#.utf8)),
        UpdateHint(
            version: "0.76.0",
            url: URL(string: "https://github.com/fx/lcb/releases/latest")!),
        "missing html_url falls back to the releases page")
    expectEqual(
        UpdateCheck.hint(
            currentVersion: "0.76.0", repo: "fx/lcb",
            releaseData: Data(releaseJSON.utf8)),
        nil, "current version does not hint")
    expectEqual(
        UpdateCheck.hint(
            currentVersion: "0.75.0", repo: nil,
            releaseData: Data(releaseJSON.utf8)),
        nil, "no configured repo (dev build) does not hint")
    expectEqual(
        UpdateCheck.hint(
            currentVersion: nil, repo: "fx/lcb",
            releaseData: Data(releaseJSON.utf8)),
        nil, "no bundle version (dev build) does not hint")

    expectEqual(
        UpdateCheck.releasesAPIURL(repo: "fx/lcb"),
        URL(string: "https://api.github.com/repos/fx/lcb/releases/latest"),
        "releases API url is built from the repo")

    // The async check must bail out before touching the network for dev
    // builds — no repo, no version, or a non-release version string.
    expectEqual(
        await UpdateCheck.check(currentVersion: "0.75.0", repo: nil), nil,
        "check without a repo resolves nil")
    expectEqual(
        await UpdateCheck.check(currentVersion: nil, repo: "fx/lcb"), nil,
        "check without a current version resolves nil")
    expectEqual(
        await UpdateCheck.check(currentVersion: "dev", repo: "fx/lcb"), nil,
        "check in a dev build resolves nil")
}

// MARK: - RestartPolicy

@MainActor func checkRestartPolicy() {
    let policy = RestartPolicy(
        maxConsecutiveCrashes: 3, stabilityWindow: 60, baseDelay: 1, maxDelay: 30)
    var state = RestartState()

    expectEqual(
        state.recordCrash(at: 100, policy: policy), .restart(after: 1, attempt: 1),
        "first crash restarts after the base delay")
    expectEqual(
        state.recordCrash(at: 110, policy: policy), .restart(after: 2, attempt: 2),
        "quick second crash doubles the delay")
    expectEqual(
        state.recordCrash(at: 120, policy: policy), .restart(after: 4, attempt: 3),
        "quick third crash doubles again")
    expectEqual(
        state.recordCrash(at: 130, policy: policy), .giveUp,
        "exceeding maxConsecutiveCrashes gives up (crash loop)")

    var stable = RestartState()
    _ = stable.recordCrash(at: 100, policy: policy)
    _ = stable.recordCrash(at: 110, policy: policy)
    expectEqual(
        stable.recordCrash(at: 300, policy: policy), .restart(after: 1, attempt: 1),
        "a crash after a stable run resets the consecutive counter")

    var capped = RestartState()
    let cappedPolicy = RestartPolicy(
        maxConsecutiveCrashes: 10, stabilityWindow: 60, baseDelay: 8, maxDelay: 10)
    _ = capped.recordCrash(at: 100, policy: cappedPolicy)
    expectEqual(
        capped.recordCrash(at: 101, policy: cappedPolicy), .restart(after: 10, attempt: 2),
        "backoff delay is capped at maxDelay")
}

// MARK: - StaleServiceState

@MainActor func checkStaleServiceState() throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }
    let stateFile = dir.appendingPathComponent("dashboard.json")

    expect(
        !StaleServiceState.clean(stateFile: stateFile),
        "missing state file -> nothing to clean")

    try "not json".write(to: stateFile, atomically: true, encoding: .utf8)
    expect(
        StaleServiceState.clean(stateFile: stateFile),
        "unreadable state file is removed")
    expect(!FileManager.default.fileExists(atPath: stateFile.path), "unreadable file is gone")

    let payload = #"{"pid": 12345, "identity": "x", "host": "127.0.0.1", "port": 8765}"#
    try payload.write(to: stateFile, atomically: true, encoding: .utf8)
    expect(
        !StaleServiceState.clean(stateFile: stateFile, isProcessAlive: { _ in true }),
        "state for a live pid is left for the Python side's identity check")
    expect(FileManager.default.fileExists(atPath: stateFile.path), "live-pid file is kept")

    expect(
        StaleServiceState.clean(stateFile: stateFile, isProcessAlive: { _ in false }),
        "state for a dead pid is stale and removed")
    expect(!FileManager.default.fileExists(atPath: stateFile.path), "dead-pid file is gone")

    try #"{"pid": -7}"#.write(to: stateFile, atomically: true, encoding: .utf8)
    expect(
        StaleServiceState.clean(stateFile: stateFile, isProcessAlive: { _ in true }),
        "nonsensical pid is treated as unreadable and removed")

    expect(
        StaleServiceState.processExists(getpid()),
        "processExists sees the current process as alive")
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

@MainActor func waitForAsync(
    timeout: TimeInterval = 20, _ condition: () async -> Bool
) async -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        if await condition() { return true }
        try? await Task.sleep(for: .milliseconds(100))
    }
    return await condition()
}

/// A service process that prints and dies immediately: the controller must
/// restart it with backoff, then give up on the crash loop with the exit code
/// and captured log surfaced for the failure view. A stale PID/state file from
/// a "previous crash" must be cleaned before the launch.
@MainActor func checkServiceControllerProcessDeath() async throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }

    let port = freePort()
    let plan = ServiceLaunchPlan(
        executable: "/bin/sh",
        arguments: ["-c", "echo boom; exit 7"],
        workingDirectory: dir,
        logFile: dir.appendingPathComponent("logs/service.log"))

    // Simulate a crashed previous run: a state file whose pid cannot exist.
    try FileManager.default.createDirectory(
        at: dir.appendingPathComponent(".runtime"), withIntermediateDirectories: true)
    try #"{"pid": 987654, "identity": "x", "host": "127.0.0.1", "port": 8765}"#
        .write(to: plan.stateFile, atomically: true, encoding: .utf8)

    let controller = ServiceController(
        plan: plan, host: "127.0.0.1", port: port, timeout: 15,
        restartPolicy: RestartPolicy(
            maxConsecutiveCrashes: 1, stabilityWindow: 60, baseDelay: 0.05, maxDelay: 0.1))

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
    expect(failed, "crash-looping service process -> failed state")
    if case let .failed(reason) = controller.state {
        expect(reason.contains("7"), "failure reason carries the exit code")
        expect(
            reason.lowercased().contains("crashed repeatedly"),
            "failure reason names the crash loop")
    }
    expect(controller.logTail().contains("boom"), "failure view can tail the captured log")
    expect(
        !FileManager.default.fileExists(atPath: plan.stateFile.path),
        "stale state file from a previous crash was cleaned before launch")
    expect(
        !controller.attachedToExternalService,
        "a launched (not attached) service is not labeled external")

    // Retry resets the tracker and crash history and relaunches; the same
    // doomed plan crash-loops to failed again.
    controller.retry()
    let failedAgain = await waitFor {
        if case .failed = controller.state { return true }
        return false
    }
    expect(failedAgain, "retry relaunches and reaches a settled state")
    controller.shutdown()
}

/// A service that crashes once and then serves: the controller must restart it
/// after the crash and reach ready again, clearing the restart marker.
@MainActor func checkServiceControllerCrashRecovery() async throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }
    try FileManager.default.createDirectory(
        at: dir.appendingPathComponent("api"), withIntermediateDirectories: true)
    try "ok".write(
        to: dir.appendingPathComponent("api/status"), atomically: true, encoding: .utf8)

    let port = freePort()
    // First launch crashes (no flag yet); the relaunch serves for real.
    let script = "if [ -f flag ]; then exec python3 -m http.server \(port) --bind 127.0.0.1; "
        + "else touch flag; exit 9; fi"
    let plan = ServiceLaunchPlan(
        executable: "/bin/sh",
        arguments: ["-c", script],
        workingDirectory: dir,
        logFile: dir.appendingPathComponent("service.log"))
    let controller = ServiceController(
        plan: plan, host: "127.0.0.1", port: port, timeout: 15,
        restartPolicy: RestartPolicy(
            maxConsecutiveCrashes: 3, stabilityWindow: 60, baseDelay: 0.05, maxDelay: 0.1))

    controller.start()
    let recovered = await waitFor {
        if case .ready = controller.state { return true }
        return false
    }
    expect(recovered, "service that crashes once is restarted and reaches ready")
    expectEqual(
        controller.restartAttempt, nil,
        "restart marker is cleared once the service is ready again")
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
    expect(
        reattached.attachedToExternalService,
        "reused service is labeled as externally owned (attach mode)")
    expect(
        !controller.attachedToExternalService,
        "the controller that launched the service is not in attach mode")

    // Quitting the app in attach mode must leave the CLI-owned service
    // untouched: after shutdown() the service still answers.
    reattached.shutdown()
    let stillAnswering = await waitForAsync {
        var request = URLRequest(url: reattached.healthURL)
        request.timeoutInterval = 2
        guard let (_, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse
        else { return false }
        return (200..<300).contains(http.statusCode)
    }
    expect(stillAnswering, "shutdown in attach mode leaves the external service running")

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
    try checkBundledRuntime()
    try checkAboutInfo()
    await checkUpdateCheck()
    checkRestartPolicy()
    try checkStaleServiceState()
    checkReportsLocation()
    try checkRecentReports()
    try checkReportDownload()
    checkDownloadNotification()
    checkLoginItemLaunch()
    checkRigSnapshotParsing()
    checkRigSnapshotEquality()
    checkStatusEventDetector()
    checkMenuBarStatus()
    checkNotificationContent()
    checkStatusPollSettings()
    await checkStatusPoller()
    await checkStatusPollerLoop()
    await checkStatusPollerHTTPFetch()
    try await checkServiceControllerProcessDeath()
    try await checkServiceControllerCrashRecovery()
    try await checkServiceControllerReadyAndReuse()
    try await checkServiceControllerLaunchError()
} catch {
    failures += 1
    print("FAIL: unexpected error thrown: \(error)")
}

print("\(passes) passed, \(failures) failed")
exit(failures == 0 ? 0 : 1)
