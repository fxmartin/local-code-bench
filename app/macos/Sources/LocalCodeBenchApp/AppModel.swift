import AppKit
import Foundation
import LocalCodeBenchKit

/// App-level state: the recorded data location and the service controller.
/// Owned by the App (not a window), so closing the dashboard window leaves the
/// service — and any in-flight runs or tier moves — running.
@MainActor
final class AppModel: ObservableObject {
    static let shared = AppModel()

    @Published private(set) var location: DataLocation?
    @Published private(set) var controller: ServiceController?
    /// The single poller feeding the menu-bar extra and notifications
    /// (story 18.2-001).
    @Published private(set) var poller: StatusPoller?
    /// A dashboard section a notification click asked to reveal; consumed by
    /// the web view via `window.showSection`.
    @Published var pendingSection: String?
    /// A newer published release, checked once on launch (best-effort, silent
    /// offline and in dev builds); the menu bar links to the download — no
    /// auto-install (story 18.3-002).
    @Published private(set) var availableUpdate: UpdateHint?

    /// Set by the main window's root view so a notification click can reopen
    /// a closed window (`openWindow` is only reachable from a view).
    var openMainWindow: (() -> Void)?

    /// Launch-at-login registration via SMAppService; the menu-bar toggle
    /// drives it (story 18.2-002).
    let launchAtLogin = LaunchAtLogin()

    let host = "127.0.0.1"
    let port = 8765

    private let store = DataLocationStore()
    private let notifier = StatusNotifier()

    private init() {
        location = store.recorded
        if location != nil { startService() }
        checkForUpdate()
    }

    var isFirstRun: Bool { location == nil }

    func completeFirstRun(with location: DataLocation) {
        store.record(location)
        self.location = location
        startService()
    }

    func shutdown() {
        poller?.stop()
        controller?.shutdown()
    }

    /// Bring the dashboard window to the front on the given section — the
    /// landing spot for a clicked notification.
    func revealDashboard(section: String) {
        pendingSection = section
        NSApp.activate(ignoringOtherApps: true)
        openMainWindow?()
    }

    /// Launch-time update check against the GitHub releases API. The repo is
    /// stamped into Info.plist (`LCBGitHubRepo`) by the build script; dev
    /// builds have neither it nor a bundle version, so the check is a no-op.
    private func checkForUpdate() {
        let info = Bundle.main.infoDictionary
        let current = info?["CFBundleShortVersionString"] as? String
        let repo = info?["LCBGitHubRepo"] as? String
        Task { [weak self] in
            guard let hint = await UpdateCheck.check(currentVersion: current, repo: repo)
            else { return }
            self?.availableUpdate = hint
        }
    }

    // MARK: Finder conveniences (story 18.2-002)

    var resultsDirectory: URL? {
        location.map { ReportsLocation.resultsDirectory(for: $0) }
    }

    var reportsDirectory: URL? {
        location.map { ReportsLocation.reportsDirectory(for: $0) }
    }

    func recentReports() -> [URL] {
        guard let reportsDirectory else { return [] }
        return RecentReports.list(in: reportsDirectory)
    }

    /// Create-if-missing so the menu action always lands somewhere real, then
    /// show the folder in Finder.
    func openResultsFolder() {
        guard let resultsDirectory else { return }
        try? FileManager.default.createDirectory(
            at: resultsDirectory, withIntermediateDirectories: true)
        NSWorkspace.shared.open(resultsDirectory)
    }

    func revealInFinder(_ url: URL) {
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    /// A dashboard-triggered report download completed: notify with "Reveal
    /// in Finder". Unbundled dev builds cannot post notifications, so they
    /// reveal the file directly — the download must never finish silently.
    func reportDownloadFinished(at url: URL) {
        if !notifier.postDownloadFinished(at: url) {
            revealInFinder(url)
        }
    }

    private func startService() {
        guard let location, controller == nil else { return }
        if case .appSupportDefault = location {
            prepareAppSupportDirectory()
        }
        // In a bundled .app, the relocatable CPython in Contents/Resources
        // runs the service; dev builds (swift run) fall back to uv / PATH.
        let runtime = BundledRuntime.locate(resourcesDirectory: Bundle.main.resourceURL)
        let plan = ServiceLaunchPlan.plan(for: location, host: host, port: port, runtime: runtime)
        let controller = ServiceController(plan: plan, host: host, port: port)
        self.controller = controller
        controller.start()
        startStatusPolling(baseURL: controller.baseURL)
    }

    /// One poller for both the menu and notifications: edge-detected events
    /// become native notifications (only while the app is in the background).
    private func startStatusPolling(baseURL: URL) {
        notifier.requestAuthorization()
        notifier.onOpenSection = { [weak self] section in
            self?.revealDashboard(section: section)
        }
        let poller = StatusPoller(baseURL: baseURL, interval: StatusPollSettings().interval)
        poller.onEvents = { [weak self] events in
            guard let self else { return }
            for event in events {
                self.notifier.post(NotificationContent.content(for: event))
            }
        }
        self.poller = poller
        poller.start()
    }

    /// The app-support data location starts from nothing: make sure the
    /// directories the service reads/writes relative to cwd exist.
    private func prepareAppSupportDirectory() {
        let root = defaultAppSupportDirectory()
        for subdirectory in ["configs", "results"] {
            try? FileManager.default.createDirectory(
                at: root.appendingPathComponent(subdirectory),
                withIntermediateDirectories: true)
        }
    }
}
