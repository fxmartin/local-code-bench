import Foundation

/// The single poller behind both the menu-bar extra and notifications (story
/// 18.2-001): one loop fetches `/api/status`, `/api/runs`, and
/// `/api/move-status`, publishes the combined snapshot for the menu, and runs
/// edge-detection so `onEvents` fires on state transitions — never on polls.
///
/// Staleness is explicit: a poll whose status fetch fails keeps the last
/// snapshot for display but flips `isStale`, so the menu can show a degraded
/// icon instead of silently stale data (e.g. a CLI-owned dashboard whose
/// process is gone).
@MainActor
public final class StatusPoller: ObservableObject {
    @Published public private(set) var snapshot: RigSnapshot?
    @Published public private(set) var isStale = false

    /// Fired from a poll that detected transitions; never fired empty.
    public var onEvents: (([StatusEvent]) -> Void)?

    private let statusURL: URL
    private let runsURL: URL
    private let moveURL: URL
    private let interval: TimeInterval
    private let fetch: @Sendable (URL) async -> Data?
    private var detector = StatusEventDetector()
    private var loop: Task<Void, Never>?

    public init(
        baseURL: URL,
        interval: TimeInterval = StatusPollSettings.defaultInterval,
        fetch: (@Sendable (URL) async -> Data?)? = nil
    ) {
        self.statusURL = baseURL.appendingPathComponent("api/status")
        self.runsURL = baseURL.appendingPathComponent("api/runs")
        self.moveURL = baseURL.appendingPathComponent("api/move-status")
        self.interval = interval
        self.fetch = fetch ?? Self.httpFetch
    }

    public func start() {
        guard loop == nil else { return }
        loop = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.pollOnce()
                try? await Task.sleep(for: .seconds(self.interval))
            }
        }
    }

    public func stop() {
        loop?.cancel()
        loop = nil
    }

    /// One fetch-parse-detect cycle; exposed so checks can drive the poller
    /// without timers.
    public func pollOnce() async {
        let status = await fetch(statusURL)
        guard status != nil else {
            // The service is not answering: keep the last snapshot for
            // display but never present it as fresh.
            isStale = true
            return
        }
        let runs = await fetch(runsURL)
        let move = await fetch(moveURL)
        let parsed = RigSnapshot.parse(status: status, runs: runs, move: move)
        snapshot = parsed
        isStale = false
        let events = detector.events(in: parsed)
        if !events.isEmpty {
            onEvents?(events)
        }
    }

    private static let httpFetch: @Sendable (URL) async -> Data? = { url in
        var request = URLRequest(url: url)
        request.timeoutInterval = 2
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse,
              (200..<300).contains(http.statusCode)
        else { return nil }
        return data
    }
}
