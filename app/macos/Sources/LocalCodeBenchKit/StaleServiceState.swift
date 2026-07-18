import Foundation

/// Detects and removes a stale dashboard PID/state file left behind by a
/// crash (`.runtime/dashboard.json`, written by `dashboard_lifecycle` on the
/// Python side). Only a file whose recorded pid is provably dead — or that is
/// unreadable — is removed; a live pid is left for the Python side's stricter
/// identity check.
public enum StaleServiceState {
    /// Returns true when a stale file was removed.
    @discardableResult
    public static func clean(
        stateFile: URL,
        fileManager: FileManager = .default,
        isProcessAlive: (Int32) -> Bool = processExists
    ) -> Bool {
        guard fileManager.fileExists(atPath: stateFile.path) else { return false }
        guard
            let data = try? Data(contentsOf: stateFile),
            let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let pid = payload["pid"] as? Int,
            pid > 0,
            pid <= Int(Int32.max)
        else {
            try? fileManager.removeItem(at: stateFile)
            return true
        }
        guard !isProcessAlive(Int32(pid)) else { return false }
        try? fileManager.removeItem(at: stateFile)
        return true
    }

    /// kill(pid, 0) probes existence without signalling; EPERM still means alive.
    public static func processExists(_ pid: Int32) -> Bool {
        kill(pid, 0) == 0 || errno == EPERM
    }
}
