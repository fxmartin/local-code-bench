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

    let host = "127.0.0.1"
    let port = 8765

    private let store = DataLocationStore()

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
        controller?.shutdown()
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
