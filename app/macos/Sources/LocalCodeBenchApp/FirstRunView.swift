import AppKit
import LocalCodeBenchKit
import SwiftUI

/// Minimal first-run panel: pick where benchmark data lives. Either the app
/// manages its own Application Support directory, or the app points at an
/// existing local-code-bench checkout so configs/results are shared with the
/// CLI. The choice is recorded and the service starts immediately.
struct FirstRunView: View {
    @EnvironmentObject private var model: AppModel
    @State private var validationError: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Welcome to Local Code Bench")
                .font(.title)
            Text("Choose where benchmark configs and results should live.")
                .foregroundStyle(.secondary)

            GroupBox {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Use the default location")
                        .font(.headline)
                    Text(defaultAppSupportDirectory().path)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                    Button("Use Default Location") {
                        model.completeFirstRun(with: .appSupportDefault)
                    }
                    .keyboardShortcut(.defaultAction)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(4)
            }

            GroupBox {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Use an existing checkout")
                        .font(.headline)
                    Text("Share configs and results with the CLI in a local-code-bench working copy.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("Choose Checkout…", action: pickCheckout)
                    if let validationError {
                        Text(validationError)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(4)
            }
        }
        .padding(32)
        .frame(maxWidth: 560, maxHeight: .infinity)
    }

    private func pickCheckout() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.message = "Select a local-code-bench checkout"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        guard CheckoutValidation.isBenchCheckout(url) else {
            validationError =
                "\(url.lastPathComponent) does not look like a local-code-bench checkout "
                + "(missing configs/ or pyproject.toml)."
            return
        }
        validationError = nil
        model.completeFirstRun(with: .checkout(url))
    }
}
