import Foundation

/// Observable lifecycle of the dashboard service as seen by the shell.
public enum ServiceState: Equatable, Sendable {
    case idle
    case starting(elapsed: TimeInterval)
    case ready
    case failed(reason: String)
}

/// Pure state machine behind the native loading view: the shell feeds it poll
/// results and process events; it decides what the window should show. Keeping
/// it free of Process/URLSession makes the startup rules testable offline.
public struct StartupTracker: Sendable {
    public let timeout: TimeInterval
    public private(set) var state: ServiceState = .idle

    public init(timeout: TimeInterval = 60) {
        self.timeout = timeout
    }

    public mutating func begin() {
        state = .starting(elapsed: 0)
    }

    public mutating func pollSucceeded() {
        if case .failed = state { return }
        state = .ready
    }

    public mutating func pollFailed(elapsed: TimeInterval) {
        guard case .starting = state else { return }
        if elapsed >= timeout {
            state = .failed(
                reason: "Service did not become ready within \(Int(timeout)) seconds.")
        } else {
            state = .starting(elapsed: elapsed)
        }
    }

    public mutating func processExited(code: Int32) {
        state = .failed(reason: "Service process exited with code \(code).")
    }
}
