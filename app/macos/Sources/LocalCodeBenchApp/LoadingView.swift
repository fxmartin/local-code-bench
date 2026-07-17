import LocalCodeBenchKit
import SwiftUI

/// Native startup state — the window never shows a white WKWebView error page
/// while the service is still coming up.
struct LoadingView: View {
    let state: ServiceState

    var body: some View {
        VStack(spacing: 16) {
            ProgressView()
                .controlSize(.large)
            Text("Starting benchmark service…")
                .font(.title3)
            if case .starting(let elapsed) = state, elapsed >= 5 {
                Text("Waiting for the dashboard to answer (\(Int(elapsed))s)")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// Startup failure: the reason plus a tail of the captured service log.
struct ServiceFailureView: View {
    let reason: String
    let logTail: String
    let logFile: URL
    let onRetry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("The benchmark service failed to start", systemImage: "exclamationmark.triangle")
                .font(.title3)
            Text(reason)
                .foregroundStyle(.secondary)

            if !logTail.isEmpty {
                ScrollView {
                    Text(logTail)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                }
                .background(.quaternary.opacity(0.5))
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }

            HStack {
                Button("Retry", action: onRetry)
                    .keyboardShortcut(.defaultAction)
                Button("Open Log") {
                    NSWorkspace.shared.open(logFile)
                }
                Spacer()
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}
