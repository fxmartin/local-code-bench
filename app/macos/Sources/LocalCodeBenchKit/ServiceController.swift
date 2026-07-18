import Foundation

/// Owns the `bench dashboard` subprocess and drives a `StartupTracker` from
/// real poll results. Lives outside any window's lifetime: closing the window
/// leaves the process (and any in-flight runs/moves) untouched, and reopening
/// just points a fresh WKWebView at the same still-running service.
///
/// Supervision (Story 18.1-002): a crash of an app-launched service triggers a
/// backoff-restart via `RestartPolicy`; crash-looping gives up with the log
/// surfaced instead of restarting forever. A service that was already running
/// (e.g. started from the CLI) is attached to, never supervised or stopped.
@MainActor
public final class ServiceController: ObservableObject {
    @Published public private(set) var state: ServiceState = .idle
    /// True when the controller attached to a service it did not launch
    /// (e.g. `bench dashboard` started from the CLI); the status UI labels
    /// this mode and `shutdown()` leaves the process untouched.
    @Published public private(set) var attachedToExternalService = false
    /// Non-nil while the service is coming back from a crash: the restart
    /// attempt number the UI shows as the interruption.
    @Published public private(set) var restartAttempt: Int?

    public let baseURL: URL
    public let healthURL: URL

    private let plan: ServiceLaunchPlan
    private let restartPolicy: RestartPolicy
    private var restartState = RestartState()
    private var tracker: StartupTracker
    private var process: Process?
    private var processIsGroupLeader = false
    private var pollTask: Task<Void, Never>?
    /// Incremented whenever polling is (re)started so a superseded poll task
    /// cannot clear the reference to its replacement.
    private var pollGeneration = 0

    public init(
        plan: ServiceLaunchPlan,
        host: String,
        port: Int,
        timeout: TimeInterval = 60,
        restartPolicy: RestartPolicy = RestartPolicy()
    ) {
        self.plan = plan
        self.restartPolicy = restartPolicy
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

        beginPolling { controller in
            // An already-running service (e.g. started from the CLI) is reused
            // rather than fought over: the port can only be bound once.
            if await Self.isHealthy(controller.healthURL) {
                controller.attachedToExternalService = true
                controller.tracker.pollSucceeded()
                controller.state = controller.tracker.state
                return
            }
            // A crash can leave last run's PID/state file behind; remove it
            // (only if its pid is provably dead) before the fresh launch.
            StaleServiceState.clean(stateFile: controller.plan.stateFile)
            controller.launchProcess()
            await controller.pollUntilSettled()
        }
    }

    /// Retry after a failure: reset the tracker and start over.
    public func retry() {
        pollGeneration += 1
        pollTask?.cancel()
        pollTask = nil
        stopProcess()
        restartState = RestartState()
        restartAttempt = nil
        tracker = StartupTracker(timeout: tracker.timeout)
        state = .idle
        start()
    }

    /// Stops the service on app termination (not on window close). An
    /// attached CLI-owned service was never launched here, so it is left
    /// untouched.
    public func shutdown() {
        pollGeneration += 1
        pollTask?.cancel()
        pollTask = nil
        stopProcess()
    }

    /// Runs `body` as the current poll task; the generation guard means a
    /// task superseded by a crash-restart can no longer clear `pollTask`.
    private func beginPolling(
        after delay: TimeInterval = 0,
        _ body: @escaping @MainActor (ServiceController) async -> Void
    ) {
        pollGeneration += 1
        let generation = pollGeneration
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            if delay > 0 {
                try? await Task.sleep(for: .seconds(delay))
            }
            guard let self, !Task.isCancelled, generation == self.pollGeneration else { return }
            await body(self)
            if generation == self.pollGeneration {
                self.pollTask = nil
            }
        }
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
                self?.handleProcessExit(code: code)
            }
        }
        do {
            try process.run()
            self.process = process
            // Best-effort own process group so quitting can kill the whole
            // tree (uv → python). Fails once the child has exec'd; the
            // service's own --exit-with-parent watchdog covers that case.
            let pid = process.processIdentifier
            processIsGroupLeader = setpgid(pid, pid) == 0
        } catch {
            tracker.processExited(code: -1)
            state = .failed(reason: "Could not launch service: \(error.localizedDescription)")
        }
    }

    /// The launched service died on its own: restart with backoff, or give up
    /// (surfacing the log) once it is crash-looping.
    private func handleProcessExit(code: Int32) {
        process = nil
        // Already failed (e.g. startup timeout): report, don't resurrect.
        if case .failed = state { return }
        let decision = restartState.recordCrash(
            at: ProcessInfo.processInfo.systemUptime, policy: restartPolicy)
        switch decision {
        case .restart(let delay, let attempt):
            restartAttempt = attempt
            tracker = StartupTracker(timeout: tracker.timeout)
            tracker.begin()
            state = tracker.state
            beginPolling(after: delay) { controller in
                controller.launchProcess()
                await controller.pollUntilSettled()
            }
        case .giveUp:
            pollGeneration += 1
            pollTask?.cancel()
            pollTask = nil
            restartAttempt = nil
            tracker.processExited(code: code)
            state = .failed(
                reason: "Service crashed repeatedly (last exit code \(code)); "
                    + "not restarting again.")
        }
    }

    private func pollUntilSettled() async {
        let started = Date()
        while !Task.isCancelled {
            if case .failed = tracker.state { state = tracker.state; return }
            let healthy = await Self.isHealthy(healthURL)
            // A crash-restart may have superseded this task mid-await; a
            // stale poll must not touch the fresh tracker.
            if Task.isCancelled { return }
            if healthy {
                tracker.pollSucceeded()
            } else {
                tracker.pollFailed(elapsed: Date().timeIntervalSince(started))
            }
            state = tracker.state
            if case .ready = tracker.state {
                restartAttempt = nil
            }
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
        let pid = process.processIdentifier
        // Kill the whole process group (uv and its python child included);
        // fall back to plain terminate when the group could not be created.
        if !processIsGroupLeader || kill(-pid, SIGTERM) != 0 {
            process.terminate()
        }
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
