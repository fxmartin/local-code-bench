import Foundation

/// The menu-bar poll interval as a user setting (nothing hardcoded): stored
/// in user defaults, clamped to a sane range, with a documented default.
/// Change it with:
///   defaults write me.fxmartin.local-code-bench statusPollIntervalSeconds -float 5
public struct StatusPollSettings {
    public static let key = "statusPollIntervalSeconds"
    public static let defaultInterval: TimeInterval = 2.0
    public static let range: ClosedRange<TimeInterval> = 0.5...60

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public var interval: TimeInterval {
        guard let stored = defaults.object(forKey: Self.key) as? NSNumber else {
            return Self.defaultInterval
        }
        return min(max(stored.doubleValue, Self.range.lowerBound), Self.range.upperBound)
    }

    public func record(_ interval: TimeInterval) {
        defaults.set(interval, forKey: Self.key)
    }
}
