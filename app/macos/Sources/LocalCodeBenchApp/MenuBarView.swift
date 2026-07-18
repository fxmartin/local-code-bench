import AppKit
import LocalCodeBenchKit
import SwiftUI

/// The menu-bar extra's icon: flips to a warning triangle when the service
/// failed or a ready service stopped answering polls — status is never
/// silently stale.
struct MenuBarIcon: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        if let controller = model.controller, let poller = model.poller {
            MenuBarIconInner(controller: controller, poller: poller)
        } else {
            Image(systemName: "gauge.with.needle")
        }
    }
}

/// Separate from `MenuBarIcon` so the nested ObservableObjects actually drive
/// re-renders (same pattern as `ServiceHostView`).
private struct MenuBarIconInner: View {
    @ObservedObject var controller: ServiceController
    @ObservedObject var poller: StatusPoller

    var body: some View {
        Image(systemName: MenuBarStatus.iconSystemImage(
            serviceState: controller.state, stale: poller.isStale))
    }
}

/// Menu content: service state, the active engine, live run and tier-move
/// progress from the status poller, a restart action when degraded, and the
/// Finder/login conveniences (story 18.2-002).
struct MenuBarContent: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        if let controller = model.controller {
            MenuBarStatusSection(controller: controller, poller: model.poller)
        }
        Button("Open Dashboard") {
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
        }
        // Unobtrusive update hint (story 18.3-002): one menu entry linking to
        // the release download; never an auto-install.
        if let update = model.availableUpdate {
            Button("Update Available — Download \(update.version)…") {
                NSWorkspace.shared.open(update.url)
            }
        }
        if !model.isFirstRun {
            Divider()
            MenuBarConveniences()
        }
        Divider()
        Button("Quit Local Code Bench") {
            NSApp.terminate(nil)
        }
    }
}

/// "Open Results Folder", the recent Epic-17 PDF reports (revealed in
/// Finder), and the launch-at-login toggle. The menu body is rebuilt each
/// time the menu opens, so the recents list is read fresh from
/// `results/reports/` (story 18.2-002).
private struct MenuBarConveniences: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Button("Open Results Folder") {
            model.openResultsFolder()
        }
        let reports = model.recentReports()
        if !reports.isEmpty {
            Menu("Recent Reports") {
                ForEach(reports, id: \.self) { report in
                    Button(report.lastPathComponent) {
                        model.revealInFinder(report)
                    }
                }
            }
        }
        if LaunchAtLogin.isSupported {
            LaunchAtLoginToggle(setting: model.launchAtLogin)
        }
    }
}

private struct LaunchAtLoginToggle: View {
    @ObservedObject var setting: LaunchAtLogin

    var body: some View {
        Toggle(
            "Launch at Login",
            isOn: Binding(
                get: { setting.isEnabled },
                set: { setting.setEnabled($0) })
        )
        // The user can also flip the login item in System Settings; re-read
        // the real status whenever the menu opens.
        .onAppear { setting.refresh() }
    }
}

private struct MenuBarStatusSection: View {
    @ObservedObject var controller: ServiceController
    var poller: StatusPoller?

    var body: some View {
        Text(serviceText)
        if let poller {
            MenuBarSnapshotSection(controller: controller, poller: poller)
        }
        Divider()
    }

    private var serviceText: String {
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

private struct MenuBarSnapshotSection: View {
    @ObservedObject var controller: ServiceController
    @ObservedObject var poller: StatusPoller

    var body: some View {
        if degraded {
            if poller.isStale, case .ready = controller.state {
                Text("Dashboard not responding")
            }
            Button("Restart Service") {
                controller.retry()
            }
        } else if case .ready = controller.state, let snapshot = poller.snapshot {
            Text(MenuBarStatus.engineLine(snapshot.engines))
            ForEach(snapshot.runs, id: \.id) { run in
                Text(MenuBarStatus.runLine(run))
            }
            if let move = snapshot.move {
                Text(MenuBarStatus.moveLine(move))
            }
        }
    }

    private var degraded: Bool {
        if case .failed = controller.state { return true }
        if case .ready = controller.state { return poller.isStale }
        return false
    }
}
