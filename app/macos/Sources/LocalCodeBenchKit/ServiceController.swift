import Foundation

/// Owns the `bench dashboard` subprocess and drives a `StartupTracker` from
/// real poll results. Lives outside any window's lifetime: closing the window
/// leaves the process (and any in-flight runs/moves) untouched, and reopening
/// just points a fresh WKWebView at the same still-running service.
@MainActor
public final class ServiceController: ObservableObject {
    @Published public private(set) var state: ServiceState = .idle

    public let baseURL: URL
    public let healthURL: URL

    private let plan: ServiceLaunchPlan
    private var tracker: StartupTracker
    private var process: Process?
    private var pollTask: Task<Void, Never>?

    public init(plan: ServiceLaunchPlan, host: String, port: Int, timeout: TimeInterval = 60) {
        self.plan = plan
        self.tracker = StartupTracker(timeout: timeout)
        self.baseURL = URL(string: "http://\(host):\(port)/")!
        self.healthURL = baseURL.appendingPathComponent("api/status")
    }

    public var logFile: URL { plan.logFile }

    /// Tail of the captured service log, for the failure view.
    public func logTail(lines: Int = 40) -> String {
        LogTail.tail(fileAt: plan.logFile, lines: lines)
    }

    /// Launches the service (unless one is already answering on the port) and
    /// polls `/api/status` until ready, timeout, or process death.
    public func start() {
        guard pollTask == nil else { return }
        tracker.begin()
        state = tracker.state

        let fileManager = FileManager.default
        try? fileManager.createDirectory(
            at: plan.logFile.deletingLastPathComponent(), withIntermediateDirectories: true)

        pollTask = Task { [weak self] in
            guard let self else { return }
            // An already-running service (e.g. started from the CLI) is reused
            // rather than fought over: the port can only be bound once.
            if await Self.isHealthy(self.healthURL) {
                self.tracker.pollSucceeded()
                self.state = self.tracker.state
                self.pollTask = nil
                return
            }
            self.launchProcess()
            await self.pollUntilSettled()
            self.pollTask = nil
        }
    }

    /// Retry after a failure: reset the tracker and start over.
    public func retry() {
        pollTask?.cancel()
        pollTask = nil
        stopProcess()
        tracker = StartupTracker(timeout: tracker.timeout)
        state = .idle
        start()
    }

    /// Stops the service on app termination (not on window close).
    public func shutdown() {
        pollTask?.cancel()
        pollTask = nil
        stopProcess()
    }

    private func launchProcess() {
        FileManager.default.createFile(atPath: plan.logFile.path, contents: nil)
        let logHandle = try? FileHandle(forWritingTo: plan.logFile)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: plan.executable)
        process.arguments = plan.arguments
        process.currentDirectoryURL = plan.workingDirectory
        if let logHandle {
            process.standardOutput = logHandle
            process.standardError = logHandle
        }
        process.terminationHandler = { [weak self] finished in
            let code = finished.terminationStatus
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.tracker.processExited(code: code)
                self.state = self.tracker.state
            }
        }
        do {
            try process.run()
            self.process = process
        } catch {
            tracker.processExited(code: -1)
            state = .failed(reason: "Could not launch service: \(error.localizedDescription)")
        }
    }

    private func pollUntilSettled() async {
        let started = Date()
        while !Task.isCancelled {
            if case .failed = tracker.state { state = tracker.state; return }
            if await Self.isHealthy(healthURL) {
                tracker.pollSucceeded()
            } else {
                tracker.pollFailed(elapsed: Date().timeIntervalSince(started))
            }
            state = tracker.state
            if case .starting = tracker.state {
                try? await Task.sleep(for: .milliseconds(500))
                continue
            }
            return
        }
    }

    private func stopProcess() {
        guard let process, process.isRunning else { return }
        process.terminationHandler = nil
        process.terminate()
        self.process = nil
    }

    private static func isHealthy(_ url: URL) async -> Bool {
        var request = URLRequest(url: url)
        request.timeoutInterval = 2
        guard let (_, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse
        else { return false }
        return (200..<300).contains(http.statusCode)
    }
}
