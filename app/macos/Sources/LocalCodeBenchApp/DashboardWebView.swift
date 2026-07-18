import AppKit
import LocalCodeBenchKit
import SwiftUI
import WebKit

/// Full-bleed WKWebView on the dashboard URL. Bridging is deliberately
/// minimal: same-origin navigations render here, anything else opens in the
/// default browser, and downloads (e.g. the Epic-17 comparison PDF) go
/// through the standard save panel.
struct DashboardWebView: NSViewRepresentable {
    let url: URL
    /// A dashboard section a notification click asked for; consumed (reset to
    /// nil) once handed to the page's `window.showSection`.
    @Binding var pendingSection: String?

    func makeCoordinator() -> Coordinator {
        Coordinator(baseURL: url)
    }

    func makeNSView(context: Context) -> WKWebView {
        let webView = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.allowsMagnification = true
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        guard let section = pendingSection else { return }
        context.coordinator.show(section: section, in: webView)
        Task { @MainActor in pendingSection = nil }
    }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
        let baseURL: URL
        /// A section requested while the page was still loading; replayed
        /// from `didFinish` (a freshly reopened window races the page load).
        private var sectionAwaitingLoad: String?

        init(baseURL: URL) {
            self.baseURL = baseURL
        }

        func show(section: String, in webView: WKWebView) {
            if webView.isLoading {
                sectionAwaitingLoad = section
                return
            }
            // Section names come from our own NotificationContent enum, never
            // from remote input; showSection is the nav's exposed switcher.
            webView.evaluateJavaScript(
                "window.showSection && window.showSection('\(section)')")
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            if let section = sectionAwaitingLoad {
                sectionAwaitingLoad = nil
                show(section: section, in: webView)
            }
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.cancel)
                return
            }
            if navigationAction.shouldPerformDownload {
                decisionHandler(.download)
                return
            }
            switch NavigationPolicy.decide(url: url, dashboardBaseURL: baseURL) {
            case .allow:
                decisionHandler(.allow)
            case .openExternally:
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
            }
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationResponse: WKNavigationResponse,
            decisionHandler: @escaping @MainActor @Sendable (WKNavigationResponsePolicy) -> Void
        ) {
            decisionHandler(navigationResponse.canShowMIMEType ? .allow : .download)
        }

        // target=_blank: external links go to the browser, same-origin loads
        // stay in this web view. Never spawn a second web view.
        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            if let url = navigationAction.request.url {
                switch NavigationPolicy.decide(url: url, dashboardBaseURL: baseURL) {
                case .allow:
                    webView.load(URLRequest(url: url))
                case .openExternally:
                    NSWorkspace.shared.open(url)
                }
            }
            return nil
        }

        // MARK: Downloads

        func webView(
            _ webView: WKWebView, navigationAction: WKNavigationAction,
            didBecome download: WKDownload
        ) {
            download.delegate = self
        }

        func webView(
            _ webView: WKWebView, navigationResponse: WKNavigationResponse,
            didBecome download: WKDownload
        ) {
            download.delegate = self
        }

        func download(
            _ download: WKDownload,
            decideDestinationUsing response: URLResponse,
            suggestedFilename: String,
            completionHandler: @escaping @MainActor @Sendable (URL?) -> Void
        ) {
            let panel = NSSavePanel()
            panel.nameFieldStringValue = suggestedFilename
            panel.canCreateDirectories = true
            completionHandler(panel.runModal() == .OK ? panel.url : nil)
        }
    }
}
