import Foundation

/// Pure formatting behind the menu-bar extra: snapshot in, display strings
/// out. Keeping this free of SwiftUI makes every line checkable offline.
public enum MenuBarStatus {
    /// The degraded icon shows whenever status can no longer be trusted: the
    /// service failed, or a ready service stopped answering polls (e.g. a
    /// CLI-owned dashboard whose process is gone). Staleness during startup is
    /// expected, not degraded.
    public static func iconSystemImage(serviceState: ServiceState, stale: Bool) -> String {
        switch serviceState {
        case .failed: "exclamationmark.triangle"
        case .ready: stale ? "exclamationmark.triangle" : "gauge.with.needle"
        case .idle, .starting: "gauge.with.needle"
        }
    }

    /// The active engine (first running one) with its version when known.
    public static func engineLine(_ engines: [EngineStatus]) -> String {
        guard let active = engines.first(where: { $0.running }) else {
            return "No engine running"
        }
        guard let version = active.engineVersion else { return "Engine: \(active.name)" }
        return "Engine: \(active.name) — \(version)"
    }

    /// One line per tracked run: live progress while running, the outcome
    /// once terminal.
    public static func runLine(_ run: RunStatus) -> String {
        let counts = "(\(run.passed) passed, \(run.failed) failed)"
        switch run.status {
        case "running":
            let suites = run.suites.joined(separator: "+")
            return "Run: \(run.model) — \(suites) \(run.completed)/\(run.total) \(counts)"
        case "failed":
            return "Run: \(run.model) — failed \(counts)"
        default:
            return "Run: \(run.model) — \(run.status) \(counts)"
        }
    }

    /// The tier move with live byte progress while running, the verdict once
    /// terminal.
    public static func moveLine(_ move: MoveStatus) -> String {
        let action = "\(move.verb.capitalized) \(move.name)"
        switch move.state {
        case "done":
            return "\(action) — done"
        case "error":
            return "\(action) — failed"
        default:
            guard move.bytesTotal > 0 else {
                return "\(action) — \(gigabytes(move.bytesDone)) copied"
            }
            let percent = Int(move.bytesDone * 100 / move.bytesTotal)
            return "\(action) — \(percent)% of \(gigabytes(move.bytesTotal))"
        }
    }

    private static func gigabytes(_ bytes: Int64) -> String {
        String(format: "%.1f GB", Double(bytes) / 1_000_000_000)
    }
}
