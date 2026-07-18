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
/// progress from the status poller, and a restart action when degraded.
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
        Divider()
        Button("Quit Local Code Bench") {
            NSApp.terminate(nil)
        }
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
