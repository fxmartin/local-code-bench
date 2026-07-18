import Foundation

/// One engine row from `GET /api/status` — only the fields the menu bar shows.
public struct EngineStatus: Equatable, Sendable {
    public let name: String
    public let running: Bool
    public let healthy: Bool
    public let engineVersion: String?

    public init(name: String, running: Bool, healthy: Bool, engineVersion: String?) {
        self.name = name
        self.running = running
        self.healthy = healthy
        self.engineVersion = engineVersion
    }
}

/// One tracked run from `GET /api/runs` (story 09.4-001's live payload).
public struct RunStatus: Equatable, Sendable {
    public let id: String
    public let model: String
    public let suites: [String]
    public let status: String
    public let total: Int
    public let completed: Int
    public let passed: Int
    public let failed: Int
    public let error: String?

    public init(
        id: String, model: String, suites: [String], status: String,
        total: Int, completed: Int, passed: Int, failed: Int, error: String?
    ) {
        self.id = id
        self.model = model
        self.suites = suites
        self.status = status
        self.total = total
        self.completed = completed
        self.passed = passed
        self.failed = failed
        self.error = error
    }

    /// The orchestrator's terminal statuses; everything else is in flight.
    public var isTerminal: Bool { status == "completed" || status == "failed" }
}

/// The current/last tier move from `GET /api/move-status` (story 12.6-003).
public struct MoveStatus: Equatable, Sendable {
    public let verb: String
    public let name: String
    public let format: String
    public let state: String
    public let bytesTotal: Int64
    public let bytesDone: Int64
    public let error: String?

    public init(
        verb: String, name: String, format: String, state: String,
        bytesTotal: Int64, bytesDone: Int64, error: String?
    ) {
        self.verb = verb
        self.name = name
        self.format = format
        self.state = state
        self.bytesTotal = bytesTotal
        self.bytesDone = bytesDone
        self.error = error
    }

    /// The MoveWorker's terminal states; "running" is the only live one.
    public var isTerminal: Bool { state == "done" || state == "error" }
}

/// Everything the rig reports in one poll: engines, runs, and the tier move.
/// Parsing is tolerant — a missing or malformed payload yields an empty part,
/// never a crash, so one broken endpoint cannot blank the whole menu.
public struct RigSnapshot: Equatable, Sendable {
    public let engines: [EngineStatus]
    public let runs: [RunStatus]
    public let move: MoveStatus?

    public init(engines: [EngineStatus], runs: [RunStatus], move: MoveStatus?) {
        self.engines = engines
        self.runs = runs
        self.move = move
    }

    public static func parse(status: Data?, runs: Data?, move: Data?) -> RigSnapshot {
        RigSnapshot(
            engines: parseEngines(status),
            runs: parseRuns(runs),
            move: parseMove(move))
    }

    private static func object(_ data: Data?) -> [String: Any]? {
        guard let data else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    private static func parseEngines(_ data: Data?) -> [EngineStatus] {
        guard let rows = object(data)?["inferencers"] as? [[String: Any]] else { return [] }
        return rows.compactMap { row in
            guard let name = row["name"] as? String else { return nil }
            return EngineStatus(
                name: name,
                running: row["running"] as? Bool ?? false,
                healthy: row["healthy"] as? Bool ?? false,
                engineVersion: row["engine_version"] as? String)
        }
    }

    private static func parseRuns(_ data: Data?) -> [RunStatus] {
        guard let rows = object(data)?["runs"] as? [[String: Any]] else { return [] }
        return rows.compactMap { row in
            guard let id = row["run_id"] as? String else { return nil }
            return RunStatus(
                id: id,
                model: row["model"] as? String ?? "",
                suites: row["suites"] as? [String] ?? [],
                status: row["status"] as? String ?? "",
                total: row["total"] as? Int ?? 0,
                completed: row["completed"] as? Int ?? 0,
                passed: row["passed"] as? Int ?? 0,
                failed: row["failed"] as? Int ?? 0,
                error: row["error"] as? String)
        }
    }

    private static func parseMove(_ data: Data?) -> MoveStatus? {
        guard let job = object(data)?["job"] as? [String: Any],
              let verb = job["verb"] as? String
        else { return nil }
        return MoveStatus(
            verb: verb,
            name: job["name"] as? String ?? "",
            format: job["format"] as? String ?? "",
            state: job["state"] as? String ?? "",
            bytesTotal: (job["bytes_total"] as? NSNumber)?.int64Value ?? 0,
            bytesDone: (job["bytes_done"] as? NSNumber)?.int64Value ?? 0,
            error: job["error"] as? String)
    }
}
