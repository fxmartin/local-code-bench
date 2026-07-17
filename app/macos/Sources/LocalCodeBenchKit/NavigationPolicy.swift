import Foundation

public enum NavigationDecision: Equatable, Sendable {
    case allow
    case openExternally
}

/// The only JS↔Swift "bridge" the shell has: navigations that stay on the
/// dashboard origin render in the WKWebView; everything else opens in the
/// user's default browser.
public enum NavigationPolicy {
    public static func decide(url: URL, dashboardBaseURL: URL) -> NavigationDecision {
        // about:blank is WKWebView's own initial/interstitial page.
        if url.scheme == "about" { return .allow }
        guard url.scheme == dashboardBaseURL.scheme,
              url.host == dashboardBaseURL.host,
              effectivePort(of: url) == effectivePort(of: dashboardBaseURL)
        else { return .openExternally }
        return .allow
    }

    private static func effectivePort(of url: URL) -> Int? {
        if let port = url.port { return port }
        switch url.scheme {
        case "http": return 80
        case "https": return 443
        default: return nil
        }
    }
}
