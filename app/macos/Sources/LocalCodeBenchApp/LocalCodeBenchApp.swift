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
        // runs continue in the background.
        MenuBarExtra("Local Code Bench", systemImage: "gauge.with.needle") {
            MenuBarContent()
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

struct MenuBarContent: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        if let controller = model.controller {
            MenuBarStatusLabel(controller: controller)
        }
        Button("Open Dashboard") {
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
        }
        Divider()
        Button("Quit Local Code Bench") {
            NSApp.terminate(nil)
        }
    }
}

private struct MenuBarStatusLabel: View {
    @ObservedObject var controller: ServiceController

    var body: some View {
        Text(statusText)
    }

    private var statusText: String {
        switch controller.state {
        case .idle: "Service: idle"
        case .starting:
            if let attempt = controller.restartAttempt {
                "Service: restarting (attempt \(attempt))…"
            } else {
                "Service: starting…"
            }
        case .ready:
            controller.attachedToExternalService
                ? "Service: running (CLI-owned)" : "Service: running (app-managed)"
        case .failed: "Service: failed"
        }
    }
}
