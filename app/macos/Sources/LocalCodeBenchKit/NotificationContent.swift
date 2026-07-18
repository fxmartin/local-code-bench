import Foundation

/// What a native notification says and where a click lands: `section` is a
/// dashboard nav name fed to the page's `window.showSection`.
public struct NotificationContent: Equatable, Sendable {
    public let title: String
    public let body: String
    public let section: String

    public init(title: String, body: String, section: String) {
        self.title = title
        self.body = body
        self.section = section
    }

    public static func content(for event: StatusEvent) -> NotificationContent {
        switch event {
        case .runFinished(let run):
            let counts = "\(run.passed) passed, \(run.failed) failed"
            if run.status == "failed" {
                let reason = run.error.map { " — \($0)" } ?? ""
                return NotificationContent(
                    title: "Benchmark run failed",
                    body: "\(run.model)\(reason) (\(counts))",
                    section: "run")
            }
            return NotificationContent(
                title: "Benchmark run completed",
                body: "\(run.model) — \(counts)",
                section: "run")
        case .moveFinished(let move):
            let action = "\(move.verb.capitalized) \(move.name) (\(move.format))"
            if move.state == "error" {
                let reason = move.error.map { " — \($0)" } ?? ""
                return NotificationContent(
                    title: "Tier move failed",
                    body: "\(action)\(reason)",
                    section: "inventory")
            }
            return NotificationContent(
                title: "Tier move completed",
                body: action,
                section: "inventory")
        }
    }
}
