import AppKit
import ServiceManagement

/// Launch-at-login via the modern SMAppService API (story 18.2-002).
/// Registration needs a real `.app` bundle; unbundled `swift run` dev builds
/// have no main-app service to register, so the toggle is hidden there.
@MainActor
final class LaunchAtLogin: ObservableObject {
    static let isSupported = Bundle.main.bundleIdentifier != nil

    @Published private(set) var isEnabled = false

    init() {
        refresh()
    }

    /// Re-reads the registration status — the user can also flip it in
    /// System Settings, so the checkmark is refreshed every time the menu opens.
    func refresh() {
        guard Self.isSupported else { return }
        isEnabled = SMAppService.mainApp.status == .enabled
    }

    func setEnabled(_ enabled: Bool) {
        guard Self.isSupported else { return }
        do {
            if enabled {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            // Registration can be denied (e.g. blocked in System Settings);
            // the refresh below keeps the checkmark truthful either way.
        }
        refresh()
    }
}
