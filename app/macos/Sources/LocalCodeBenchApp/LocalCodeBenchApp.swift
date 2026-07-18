import AppKit
import LocalCodeBenchKit
import SwiftUI

@main
struct LocalCodeBenchApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel.shared

    var body: some Scene {
        WindowGroup("Local Code Bench", id: "main") {
            RootView()
                .environmentObject(model)
                .frame(minWidth: 900, minHeight: 600)
                .background(WindowFrameAutosave(name: "LocalCodeBenchMainWindow"))
        }
        .commands {
            // The standard about panel shows only CFBundleShortVersionString;
            // replacing it lets the credits line surface the bundled harness
            // version too (Story 18.3-001).
            CommandGroup(replacing: .appInfo) {
                Button("About Local Code Bench") {
                    showAboutPanel()
                }
            }
        }

        // Keeps the app reachable while the dashboard window is closed and
        // runs continue in the background; the icon flips to a warning when
        // the service is failed or no longer answering (story 18.2-001).
        MenuBarExtra {
            MenuBarContent()
                .environmentObject(model)
        } label: {
            MenuBarIcon()
                .environmentObject(model)
        }
    }
}

/// Shows the standard about panel with both versions: the app's
/// `CFBundleShortVersionString` (mirroring `pyproject.toml`) and the harness
/// version recorded in the bundle by `scripts/build-macos-app.sh`.
@MainActor private func showAboutPanel() {
    let about = AboutInfo.resolve(
        bundleShortVersion: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String,
        bundledHarnessVersion: AboutInfo.bundledHarnessVersion(
            resourcesDirectory: Bundle.main.resourceURL))
    let credits = NSAttributedString(
        string: "Harness \(about.harnessVersion)",
        attributes: [
            .font: NSFont.systemFont(ofSize: NSFont.smallSystemFontSize),
            .foregroundColor: NSColor.secondaryLabelColor,
        ])
    NSApplication.shared.orderFrontStandardAboutPanel(options: [
        .applicationName: "Local Code Bench",
        .applicationVersion: about.appVersion,
        .credits: credits,
    ])
    NSApp.activate(ignoringOtherApps: true)
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    /// Window close must not kill in-flight runs/moves: the app (and the
    /// service process it owns) stays alive in the Dock and menu bar.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    /// Clicking the Dock icon with no open window reopens the dashboard
    /// window, which reattaches to the still-running service.
    func applicationShouldHandleReopen(
        _ sender: NSApplication, hasVisibleWindows flag: Bool
    ) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        AppModel.shared.shutdown()
    }
}
