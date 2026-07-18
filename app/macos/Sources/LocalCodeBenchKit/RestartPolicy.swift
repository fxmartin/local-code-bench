import Foundation

/// When a supervised service process dies, decides whether to restart it and
/// after what delay. Pure value types: the controller feeds crash times from a
/// monotonic clock, so backoff and crash-loop rules are testable offline.
public struct RestartPolicy: Equatable, Sendable {
    /// Consecutive crashes tolerated before giving up (crash-looping).
    public var maxConsecutiveCrashes: Int
    /// A crash this close (seconds) to the previous one counts as consecutive;
    /// a longer gap means the service ran stably and the counter resets.
    public var stabilityWindow: TimeInterval
    /// Delay before the first restart; doubles per consecutive crash.
    public var baseDelay: TimeInterval
    public var maxDelay: TimeInterval

    public init(
        maxConsecutiveCrashes: Int = 5,
        stabilityWindow: TimeInterval = 60,
        baseDelay: TimeInterval = 1,
        maxDelay: TimeInterval = 30
    ) {
        self.maxConsecutiveCrashes = maxConsecutiveCrashes
        self.stabilityWindow = stabilityWindow
        self.baseDelay = baseDelay
        self.maxDelay = maxDelay
    }
}

public enum RestartDecision: Equatable, Sendable {
    case restart(after: TimeInterval, attempt: Int)
    case giveUp
}

/// Mutable crash history driving `RestartPolicy` decisions.
public struct RestartState: Sendable {
    private var lastCrash: TimeInterval?
    private var consecutiveCrashes = 0

    public init() {}

    public mutating func recordCrash(
        at now: TimeInterval, policy: RestartPolicy
    ) -> RestartDecision {
        if let lastCrash, now - lastCrash < policy.stabilityWindow {
            consecutiveCrashes += 1
        } else {
            consecutiveCrashes = 1
        }
        lastCrash = now
        guard consecutiveCrashes <= policy.maxConsecutiveCrashes else { return .giveUp }
        let doublings = consecutiveCrashes - 1
        let delay = min(policy.baseDelay * pow(2, Double(doublings)), policy.maxDelay)
        return .restart(after: delay, attempt: consecutiveCrashes)
    }
}
