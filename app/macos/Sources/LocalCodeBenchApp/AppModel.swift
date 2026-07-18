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

    /// Set by the main window's root view so a notification click can reopen
    /// a closed window (`openWindow` is only reachable from a view).
    var openMainWindow: (() -> Void)?

    let host = "127.0.0.1"
    let port = 8765

    private let store = DataLocationStore()
    private let notifier = StatusNotifier()

    private init() {
        location = store.recorded
        if location != nil { startService() }
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
