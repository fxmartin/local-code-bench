import Foundation

/// Something worth a native notification: a run or tier move reaching a
/// terminal state.
public enum StatusEvent: Equatable, Sendable {
    case runFinished(RunStatus)
    case moveFinished(MoveStatus)
}

/// Turns a stream of polled snapshots into discrete events: state
/// *transitions* fire, repeated observations of the same state do not. The
/// first snapshot is a baseline — outcomes that predate the app's launch
/// (a finished run still listed, last night's move verdict) are swallowed so
/// startup never replays history as fresh notifications.
public struct StatusEventDetector: Sendable {
    private var baselined = false
    /// Last observed status per run id; a run missing here is unseen.
    private var runStatuses: [String: String] = [:]
    private var lastMove: MoveStatus?

    public init() {}

    public mutating func events(in snapshot: RigSnapshot) -> [StatusEvent] {
        var events: [StatusEvent] = []

        for run in snapshot.runs {
            let previous = runStatuses[run.id]
            runStatuses[run.id] = run.status
            guard baselined, run.isTerminal, previous != run.status else { continue }
            // Fires on running -> terminal, and on a run first seen already
            // terminal (it started and finished between two polls).
            events.append(.runFinished(run))
        }

        if let move = snapshot.move {
            let previous = lastMove
            lastMove = move
            // A terminal job is republished by every poll; only the edge into
            // it (state flip, or a different move that finished between two
            // polls) is an event.
            if baselined, move.isTerminal, previous != move {
                events.append(.moveFinished(move))
            }
        }

        baselined = true
        return events
    }
}
