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

        // Keeps the app reachable while the dashboard window is closed and
        // runs continue in the background.
        MenuBarExtra("Local Code Bench", systemImage: "gauge.with.needle") {
            MenuBarContent()
                .environmentObject(model)
        }
    }
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
        case .starting: "Service: starting…"
        case .ready: "Service: running"
        case .failed: "Service: failed"
        }
    }
}
