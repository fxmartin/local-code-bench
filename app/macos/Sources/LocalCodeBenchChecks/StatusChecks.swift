// Checks for the menu-bar status pipeline (Story 18.2-001): payload parsing,
// edge detection, menu formatting, notification content, poll settings, and
// the poller itself (with an injected fetcher — no live dashboard needed).
import Foundation
import LocalCodeBenchKit

// MARK: - Fixtures

let statusJSON = """
{"inferencers": [
  {"name": "mlx", "installed": true, "lifecycle": "server", "running": true,
   "pid": 4242, "port": 8080, "healthy": true, "engine_version": "mlx-lm 0.21.0",
   "detail": null},
  {"name": "ollama", "installed": true, "lifecycle": "server", "running": false,
   "pid": null, "port": 11434, "healthy": false, "engine_version": null,
   "detail": null}
]}
""".data(using: .utf8)!

let runsRunningJSON = """
{"runs": [
  {"run_id": "r1", "model": "qwen2.5-coder", "inferencer": "mlx",
   "suites": ["humaneval"], "result_file": "r1.jsonl", "status": "running",
   "total": 164, "completed": 12, "passed": 10, "failed": 2,
   "last_event": "task 12", "error": null, "remaining": 152,
   "cost_usd": null, "decode_tokens_per_second": 41.5}
]}
""".data(using: .utf8)!

let runsCompletedJSON = """
{"runs": [
  {"run_id": "r1", "model": "qwen2.5-coder", "inferencer": "mlx",
   "suites": ["humaneval"], "result_file": "r1.jsonl", "status": "completed",
   "total": 164, "completed": 164, "passed": 158, "failed": 6,
   "last_event": "done", "error": null, "remaining": 0,
   "cost_usd": 0.0, "decode_tokens_per_second": 41.5}
]}
""".data(using: .utf8)!

let runsFailedJSON = """
{"runs": [
  {"run_id": "r2", "model": "glm-4", "inferencer": "mlx",
   "suites": ["mbpp"], "result_file": "r2.jsonl", "status": "failed",
   "total": 100, "completed": 3, "passed": 2, "failed": 1,
   "last_event": null, "error": "endpoint refused connection", "remaining": 97}
]}
""".data(using: .utf8)!

let moveRunningJSON = """
{"job": {"verb": "promote", "name": "qwen2.5-coder", "format": "mlx",
 "state": "running", "bytes_total": 5000000000, "bytes_done": 2100000000,
 "elapsed_seconds": 12.5, "error": null, "result": null}}
""".data(using: .utf8)!

let moveDoneJSON = """
{"job": {"verb": "promote", "name": "qwen2.5-coder", "format": "mlx",
 "state": "done", "bytes_total": 5000000000, "bytes_done": 5000000000,
 "elapsed_seconds": 40.1, "error": null,
 "result": {"promoted": {"name": "qwen2.5-coder", "tier": "local"}}}}
""".data(using: .utf8)!

let moveErrorJSON = """
{"job": {"verb": "demote", "name": "glm-4", "format": "gguf",
 "state": "error", "bytes_total": 0, "bytes_done": 0,
 "elapsed_seconds": 1.0, "error": "external repo offline", "result": null}}
""".data(using: .utf8)!

let moveNullJSON = #"{"job": null}"#.data(using: .utf8)!

// MARK: - RigSnapshot parsing

@MainActor func checkRigSnapshotParsing() {
    let snapshot = RigSnapshot.parse(
        status: statusJSON, runs: runsRunningJSON, move: moveRunningJSON)

    expectEqual(snapshot.engines.count, 2, "status payload parses one engine per row")
    let mlx = snapshot.engines[0]
    expectEqual(mlx.name, "mlx", "engine name parses")
    expect(mlx.running && mlx.healthy, "running/healthy flags parse")
    expectEqual(mlx.engineVersion, "mlx-lm 0.21.0", "engine version parses")
    expectEqual(snapshot.engines[1].engineVersion, nil, "null engine version parses as nil")

    expectEqual(snapshot.runs.count, 1, "runs payload parses")
    let run = snapshot.runs[0]
    expectEqual(run.id, "r1", "run id parses")
    expectEqual(run.model, "qwen2.5-coder", "run model parses")
    expectEqual(run.suites, ["humaneval"], "run suites parse")
    expectEqual(run.status, "running", "run status parses")
    expectEqual(run.total, 164, "run total parses")
    expectEqual(run.completed, 12, "run completed parses")
    expectEqual(run.passed, 10, "run passed parses")
    expectEqual(run.failed, 2, "run failed parses")
    expect(!run.isTerminal, "a running run is not terminal")

    guard let move = snapshot.move else {
        expect(false, "move payload parses")
        return
    }
    expectEqual(move.verb, "promote", "move verb parses")
    expectEqual(move.name, "qwen2.5-coder", "move name parses")
    expectEqual(move.state, "running", "move state parses")
    expectEqual(move.bytesTotal, 5_000_000_000, "move bytes_total parses")
    expectEqual(move.bytesDone, 2_100_000_000, "move bytes_done parses")
    expect(!move.isTerminal, "a running move is not terminal")

    let failedRun = RigSnapshot.parse(status: nil, runs: runsFailedJSON, move: moveErrorJSON)
    expectEqual(failedRun.runs[0].error, "endpoint refused connection", "run error parses")
    expect(failedRun.runs[0].isTerminal, "a failed run is terminal")
    expectEqual(failedRun.move?.error, "external repo offline", "move error parses")
    expect(failedRun.move?.isTerminal == true, "an errored move is terminal")

    let empty = RigSnapshot.parse(status: nil, runs: nil, move: nil)
    expect(empty.engines.isEmpty && empty.runs.isEmpty && empty.move == nil,
        "missing payloads parse to an empty snapshot")

    let garbage = "nope".data(using: .utf8)!
    let junk = RigSnapshot.parse(status: garbage, runs: garbage, move: garbage)
    expect(junk.engines.isEmpty && junk.runs.isEmpty && junk.move == nil,
        "malformed payloads parse to an empty snapshot, not a crash")

    let nullJob = RigSnapshot.parse(status: nil, runs: nil, move: moveNullJSON)
    expectEqual(nullJob.move, nil, "job: null (no move yet) parses as no move")
}

// MARK: - StatusEventDetector

@MainActor func checkStatusEventDetector() {
    var detector = StatusEventDetector()

    // Baseline: a snapshot full of already-terminal history fires nothing —
    // app launch must not replay old outcomes as notifications.
    let history = RigSnapshot.parse(status: nil, runs: runsCompletedJSON, move: moveDoneJSON)
    expectEqual(detector.events(in: history).count, 0, "baseline snapshot fires no events")

    // Repeated polls of the same state fire nothing (edges, not levels).
    expectEqual(detector.events(in: history).count, 0, "unchanged poll fires no events")

    var live = StatusEventDetector()
    let running = RigSnapshot.parse(status: nil, runs: runsRunningJSON, move: moveRunningJSON)
    expectEqual(live.events(in: running).count, 0, "baseline with live work fires no events")
    expectEqual(live.events(in: running).count, 0, "running poll fires no events")

    let finished = RigSnapshot.parse(status: nil, runs: runsCompletedJSON, move: moveDoneJSON)
    let events = live.events(in: finished)
    expectEqual(events.count, 2, "run and move completions each fire one event")
    expect(events.contains { if case .runFinished(let r) = $0 { return r.id == "r1" }; return false },
        "run completion event carries the run")
    expect(events.contains { if case .moveFinished(let m) = $0 { return m.state == "done" }; return false },
        "move completion event carries the verdict")
    expectEqual(live.events(in: finished).count, 0, "terminal state does not re-fire on later polls")

    // A run that appears already terminal *after* baseline (finished between
    // polls) still fires; the errored move likewise.
    let surprise = RigSnapshot.parse(status: nil, runs: runsFailedJSON, move: moveErrorJSON)
    let surpriseEvents = live.events(in: surprise)
    expectEqual(surpriseEvents.count, 2, "post-baseline terminal newcomers fire events")
    expect(surpriseEvents.contains {
        if case .runFinished(let r) = $0 { return r.status == "failed" }; return false
    }, "failed run fires a runFinished event")
    expect(surpriseEvents.contains {
        if case .moveFinished(let m) = $0 { return m.state == "error" }; return false
    }, "errored move fires a moveFinished event")
}

// MARK: - MenuBarStatus formatting

@MainActor func checkMenuBarStatus() {
    expectEqual(
        MenuBarStatus.iconSystemImage(serviceState: .ready, stale: false),
        "gauge.with.needle", "healthy service keeps the normal icon")
    expectEqual(
        MenuBarStatus.iconSystemImage(serviceState: .failed(reason: "x"), stale: false),
        "exclamationmark.triangle", "failed service shows the degraded icon")
    expectEqual(
        MenuBarStatus.iconSystemImage(serviceState: .ready, stale: true),
        "exclamationmark.triangle", "stale polling shows the degraded icon")
    expectEqual(
        MenuBarStatus.iconSystemImage(serviceState: .starting(elapsed: 1), stale: true),
        "gauge.with.needle", "staleness while still starting is not degraded")

    let snapshot = RigSnapshot.parse(status: statusJSON, runs: nil, move: nil)
    expectEqual(
        MenuBarStatus.engineLine(snapshot.engines),
        "Engine: mlx — mlx-lm 0.21.0", "engine line names the running engine + version")
    expectEqual(
        MenuBarStatus.engineLine([]), "No engine running", "no engines -> idle engine line")
    let versionless = [EngineStatus(name: "lm-studio", running: true, healthy: true, engineVersion: nil)]
    expectEqual(
        MenuBarStatus.engineLine(versionless), "Engine: lm-studio",
        "engine line omits a missing version")

    let running = RigSnapshot.parse(status: nil, runs: runsRunningJSON, move: nil).runs[0]
    expectEqual(
        MenuBarStatus.runLine(running),
        "Run: qwen2.5-coder — humaneval 12/164 (10 passed, 2 failed)",
        "run line shows live suite progress")
    let done = RigSnapshot.parse(status: nil, runs: runsCompletedJSON, move: nil).runs[0]
    expectEqual(
        MenuBarStatus.runLine(done),
        "Run: qwen2.5-coder — completed (158 passed, 6 failed)",
        "completed run line shows the outcome")

    let move = RigSnapshot.parse(status: nil, runs: nil, move: moveRunningJSON).move!
    expectEqual(
        MenuBarStatus.moveLine(move),
        "Promote qwen2.5-coder — 42% of 5.0 GB",
        "move line shows live byte progress")
    let errored = RigSnapshot.parse(status: nil, runs: nil, move: moveErrorJSON).move!
    expectEqual(
        MenuBarStatus.moveLine(errored),
        "Demote glm-4 — failed", "errored move line names the failure")
    let finished = RigSnapshot.parse(status: nil, runs: nil, move: moveDoneJSON).move!
    expectEqual(
        MenuBarStatus.moveLine(finished),
        "Promote qwen2.5-coder — done", "finished move line reports done")

    let zeroTotal = MoveStatus(
        verb: "promote", name: "m", format: "gguf", state: "running",
        bytesTotal: 0, bytesDone: 0, error: nil)
    expectEqual(
        MenuBarStatus.moveLine(zeroTotal), "Promote m — 0.0 GB copied",
        "zero-total move avoids a division by zero")

    let failedRun = RigSnapshot.parse(status: nil, runs: runsFailedJSON, move: nil).runs[0]
    expectEqual(
        MenuBarStatus.runLine(failedRun),
        "Run: glm-4 — failed (2 passed, 1 failed)",
        "failed run line names the failure")
}

// MARK: - Snapshot equality (synthesized conformances used by edge detection)

@MainActor func checkRigSnapshotEquality() {
    let a = RigSnapshot.parse(status: statusJSON, runs: runsRunningJSON, move: moveRunningJSON)
    let b = RigSnapshot.parse(status: statusJSON, runs: runsRunningJSON, move: moveRunningJSON)
    expectEqual(a, b, "identical payloads parse to equal snapshots")
    expectEqual(a.engines[0], b.engines[0], "engine rows compare equal")
    expectEqual(a.runs[0], b.runs[0], "run rows compare equal")
    expectEqual(a.move, b.move, "move jobs compare equal")

    let c = RigSnapshot.parse(status: statusJSON, runs: runsCompletedJSON, move: moveDoneJSON)
    expect(a != c, "different run/move states compare unequal")
    expect(a.engines[0] != a.engines[1], "different engine rows compare unequal")
}

// MARK: - NotificationContent

@MainActor func checkNotificationContent() {
    let done = RigSnapshot.parse(status: nil, runs: runsCompletedJSON, move: nil).runs[0]
    let completed = NotificationContent.content(for: .runFinished(done))
    expectEqual(completed.title, "Benchmark run completed", "completed run notification title")
    expect(completed.body.contains("qwen2.5-coder"), "run notification names the model")
    expect(completed.body.contains("158"), "run notification carries the pass count")
    expect(completed.body.contains("6"), "run notification carries the failure count")
    expectEqual(completed.section, "run", "run notification opens the Run section")

    let failedRun = RigSnapshot.parse(status: nil, runs: runsFailedJSON, move: nil).runs[0]
    let failed = NotificationContent.content(for: .runFinished(failedRun))
    expectEqual(failed.title, "Benchmark run failed", "failed run notification title")
    expect(failed.body.contains("endpoint refused connection"), "failed run body carries the reason")

    let doneMove = RigSnapshot.parse(status: nil, runs: nil, move: moveDoneJSON).move!
    let moved = NotificationContent.content(for: .moveFinished(doneMove))
    expectEqual(moved.title, "Tier move completed", "finished move notification title")
    expect(moved.body.contains("qwen2.5-coder"), "move notification names the model")
    expectEqual(moved.section, "inventory", "move notification opens the Inventory section")

    let badMove = RigSnapshot.parse(status: nil, runs: nil, move: moveErrorJSON).move!
    let moveFailed = NotificationContent.content(for: .moveFinished(badMove))
    expectEqual(moveFailed.title, "Tier move failed", "errored move notification title")
    expect(moveFailed.body.contains("external repo offline"), "errored move body carries the error")

    // Failures without a reason (the backend reported no error string) omit
    // the dash instead of printing a dangling separator.
    let silentRun = RunStatus(
        id: "r9", model: "m", suites: ["mbpp"], status: "failed",
        total: 10, completed: 1, passed: 0, failed: 1, error: nil)
    expectEqual(
        NotificationContent.content(for: .runFinished(silentRun)),
        NotificationContent(
            title: "Benchmark run failed", body: "m (0 passed, 1 failed)", section: "run"),
        "reason-less failed run notification omits the dash")

    let silentMove = MoveStatus(
        verb: "demote", name: "m", format: "gguf", state: "error",
        bytesTotal: 0, bytesDone: 0, error: nil)
    expectEqual(
        NotificationContent.content(for: .moveFinished(silentMove)),
        NotificationContent(
            title: "Tier move failed", body: "Demote m (gguf)", section: "inventory"),
        "reason-less errored move notification omits the dash")
}

// MARK: - StatusPollSettings

@MainActor func checkStatusPollSettings() {
    let suite = "lcb-checks-\(UUID().uuidString)"
    guard let defaults = UserDefaults(suiteName: suite) else {
        expect(false, "UserDefaults suite creation for poll settings")
        return
    }
    defer { defaults.removePersistentDomain(forName: suite) }

    let settings = StatusPollSettings(defaults: defaults)
    expectEqual(settings.interval, 2.0, "poll interval defaults to 2 seconds")

    settings.record(5.0)
    expectEqual(settings.interval, 5.0, "recorded poll interval round-trips")

    settings.record(0.01)
    expectEqual(settings.interval, 0.5, "too-small interval clamps to the floor")

    settings.record(3600)
    expectEqual(settings.interval, 60, "too-large interval clamps to the ceiling")

    defaults.set("garbage", forKey: StatusPollSettings.key)
    expectEqual(settings.interval, 2.0, "non-numeric stored value falls back to the default")
}

// MARK: - StatusPoller

@MainActor func checkStatusPoller() async {
    let base = URL(string: "http://127.0.0.1:8765/")!

    // Healthy backend: snapshot populated, not stale, events fire on edges.
    final class FetchScript: @unchecked Sendable {
        var responses: [String: Data] = [:]
        func fetch(_ url: URL) async -> Data? { responses[url.path] }
    }
    let script = FetchScript()
    script.responses = [
        "/api/status": statusJSON,
        "/api/runs": runsRunningJSON,
        "/api/move-status": moveRunningJSON,
    ]

    var received: [StatusEvent] = []
    let poller = StatusPoller(baseURL: base, fetch: { await script.fetch($0) })
    poller.onEvents = { received.append(contentsOf: $0) }

    await poller.pollOnce()
    expect(poller.snapshot != nil, "poll populates the snapshot")
    expectEqual(poller.snapshot?.engines.count, 2, "poll parses the status payload")
    expectEqual(poller.snapshot?.runs.first?.status, "running", "poll parses the runs payload")
    expectEqual(poller.snapshot?.move?.state, "running", "poll parses the move payload")
    expect(!poller.isStale, "healthy poll is not stale")
    expectEqual(received.count, 0, "baseline poll fires no events")

    script.responses["/api/runs"] = runsCompletedJSON
    script.responses["/api/move-status"] = moveDoneJSON
    await poller.pollOnce()
    expectEqual(received.count, 2, "run + move completion edges fire through onEvents")

    await poller.pollOnce()
    expectEqual(received.count, 2, "settled state fires no further events")

    // Dead backend: snapshot is kept but marked stale — never silently stale.
    script.responses = [:]
    await poller.pollOnce()
    expect(poller.isStale, "failed status poll marks the data stale")
    expect(poller.snapshot != nil, "last snapshot is kept for display while stale")
    expectEqual(received.count, 2, "a dead backend fires no phantom events")

    // Recovery clears the stale flag.
    script.responses = ["/api/status": statusJSON, "/api/runs": runsCompletedJSON,
                        "/api/move-status": moveDoneJSON]
    await poller.pollOnce()
    expect(!poller.isStale, "recovered poll clears the stale flag")
    expectEqual(received.count, 2, "recovery does not replay old terminal states")
}

// MARK: - StatusPoller loop (start/stop)

actor PollCounter {
    private(set) var count = 0
    func bump() { count += 1 }
}

@MainActor func checkStatusPollerLoop() async {
    let counter = PollCounter()
    let poller = StatusPoller(
        baseURL: URL(string: "http://127.0.0.1:8765/")!,
        interval: 0.02,
        fetch: { _ in
            await counter.bump()
            return statusJSON
        })

    poller.start()
    poller.start() // second start must not spawn a second loop

    var waited = 0
    while await counter.count == 0, waited < 200 {
        try? await Task.sleep(for: .milliseconds(10))
        waited += 1
    }
    expect(poller.snapshot != nil, "start() drives polls on the loop")
    expect(!poller.isStale, "looped poll of a healthy backend is not stale")

    poller.stop()
    poller.stop() // stop is idempotent
    // Let any in-flight cycle finish, then confirm the loop is dead.
    try? await Task.sleep(for: .milliseconds(60))
    let settled = await counter.count
    try? await Task.sleep(for: .milliseconds(100))
    let later = await counter.count
    expectEqual(later, settled, "stop() halts the poll loop")
}

// MARK: - StatusPoller default HTTP fetcher

/// A minimal one-thread HTTP server answering every request with `body`, so
/// the poller's real URLSession fetcher can be exercised without a live
/// dashboard. Returns the bound port and the listening fd (close it to stop).
func startTinyHTTPServer(body: Data) -> (port: UInt16, fd: Int32)? {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    guard fd >= 0 else { return nil }
    var yes: Int32 = 1
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))

    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = 0 // kernel-assigned port
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    let bound = withUnsafePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    guard bound == 0, listen(fd, 8) == 0 else {
        close(fd)
        return nil
    }

    var assigned = sockaddr_in()
    var len = socklen_t(MemoryLayout<sockaddr_in>.size)
    _ = withUnsafeMutablePointer(to: &assigned) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &len) }
    }
    let port = UInt16(bigEndian: assigned.sin_port)

    Thread.detachNewThread {
        while true {
            let client = accept(fd, nil, nil)
            if client < 0 { break } // listening fd closed -> shut down
            var request = [UInt8](repeating: 0, count: 4096)
            _ = recv(client, &request, request.count, 0)
            let header = "HTTP/1.1 200 OK\r\nContent-Length: \(body.count)\r\n"
                + "Content-Type: application/json\r\nConnection: close\r\n\r\n"
            _ = header.data(using: .utf8)!.withUnsafeBytes {
                send(client, $0.baseAddress, $0.count, 0)
            }
            _ = body.withUnsafeBytes { send(client, $0.baseAddress, $0.count, 0) }
            close(client)
        }
    }
    return (port, fd)
}

@MainActor func checkStatusPollerHTTPFetch() async {
    // Nothing listens on port 1: the default fetcher must fail the poll and
    // mark the (absent) data stale, not hang or crash.
    let dead = StatusPoller(baseURL: URL(string: "http://127.0.0.1:1/")!)
    await dead.pollOnce()
    expect(dead.isStale, "default fetcher marks a dead service stale")
    expect(dead.snapshot == nil, "no snapshot is invented for a dead service")

    // A live server: the default fetcher reads the payload over real HTTP.
    guard let server = startTinyHTTPServer(body: statusJSON) else {
        expect(false, "tiny HTTP server starts for the fetcher check")
        return
    }
    defer { close(server.fd) }

    let live = StatusPoller(baseURL: URL(string: "http://127.0.0.1:\(server.port)/")!)
    await live.pollOnce()
    expect(!live.isStale, "default fetcher reads a live service")
    expectEqual(live.snapshot?.engines.count, 2, "default fetcher returns the status payload")
}
