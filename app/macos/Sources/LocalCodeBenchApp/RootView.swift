import LocalCodeBenchKit
import SwiftUI

/// Window content: first-run panel until a data location is recorded, then the
/// native loading / failure states while the service starts, then the
/// dashboard itself.
struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        content
            .onAppear {
                // Lets a notification click reopen a closed window:
                // openWindow is only reachable from a view's environment.
                model.openMainWindow = { openWindow(id: "main") }
            }
    }

    @ViewBuilder private var content: some View {
        if model.isFirstRun {
            FirstRunView()
        } else if let controller = model.controller {
            ServiceHostView(controller: controller, pendingSection: $model.pendingSection)
        } else {
            ProgressView()
        }
    }
}

/// Switches on the live service state; separate from RootView so the nested
/// ObservableObject (the controller) actually drives re-renders.
private struct ServiceHostView: View {
    @ObservedObject var controller: ServiceController
    @Binding var pendingSection: String?

    var body: some View {
        switch controller.state {
        case .idle, .starting:
            LoadingView(state: controller.state)
        case .ready:
            DashboardWebView(url: controller.baseURL, pendingSection: $pendingSection)
                .ignoresSafeArea()
        case .failed(let reason):
            ServiceFailureView(
                reason: reason,
                logTail: controller.logTail(),
                logFile: controller.logFile,
                onRetry: { controller.retry() })
        }
    }
}
