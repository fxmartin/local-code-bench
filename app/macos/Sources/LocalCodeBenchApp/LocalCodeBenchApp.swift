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

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    /// A launch-at-login start (SMAppService) begins quietly in the menu bar:
    /// the opening Apple event carries 'lgit', and the dashboard window it
    /// would otherwise splash across login is closed (story 18.2-002).
    func applicationDidFinishLaunching(_ notification: Notification) {
        guard Self.launchedAsLoginItem() else { return }
        for window in NSApp.windows
        where window.identifier?.rawValue.hasPrefix("main") == true {
            window.close()
        }
    }

    private static func launchedAsLoginItem() -> Bool {
        guard let event = NSAppleEventManager.shared().currentAppleEvent else { return false }
        return LoginItemLaunch.isLoginItemLaunch(
            eventClass: event.eventClass,
            eventID: event.eventID,
            propData: event.paramDescriptor(
                forKeyword: AEKeyword(LoginItemLaunch.propDataKeyword))?.enumCodeValue)
    }

    /// Window close must not kill in-flight runs/moves: the app (and the
    /// service process it owns) stays alive in the Dock and menu bar.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    /// Dock menu: "Open Results Folder" plus the most recent Epic-17 PDF
    /// reports, each revealed in Finder (story 18.2-002).
    func applicationDockMenu(_ sender: NSApplication) -> NSMenu? {
        let model = AppModel.shared
        guard model.resultsDirectory != nil else { return nil }
        let menu = NSMenu()
        let open = NSMenuItem(
            title: "Open Results Folder",
            action: #selector(openResultsFolder(_:)), keyEquivalent: "")
        open.target = self
        menu.addItem(open)
        let reports = model.recentReports()
        if !reports.isEmpty {
            menu.addItem(.separator())
            for report in reports {
                let item = NSMenuItem(
                    title: report.lastPathComponent,
                    action: #selector(revealReport(_:)), keyEquivalent: "")
                item.target = self
                item.representedObject = report
                menu.addItem(item)
            }
        }
        return menu
    }

    @objc private func openResultsFolder(_ sender: Any?) {
        AppModel.shared.openResultsFolder()
    }

    @objc private func revealReport(_ sender: NSMenuItem) {
        guard let report = sender.representedObject as? URL else { return }
        AppModel.shared.revealInFinder(report)
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
