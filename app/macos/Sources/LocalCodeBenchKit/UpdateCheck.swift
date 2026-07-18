import Foundation

/// A newer published release worth hinting at: the version to show and the
/// page to open. Surfaced as an unobtrusive menu-bar link — never an
/// auto-install (Story 18.3-002).
public struct UpdateHint: Equatable, Sendable {
    public let version: String
    public let url: URL

    public init(version: String, url: URL) {
        self.version = version
        self.url = url
    }
}

/// The fields the update check needs from GitHub's `releases/latest` payload.
public struct LatestRelease: Equatable, Sendable {
    public let tag: String
    public let url: URL?

    public init(tag: String, url: URL?) {
        self.tag = tag
        self.url = url
    }
}

/// Best-effort "update available" check (Story 18.3-002): on launch the app
/// asks the GitHub releases API for the latest tag and, only when it is
/// strictly newer than the running bundle's version, produces an `UpdateHint`.
/// Everything is silent-on-failure — offline, rate-limited, malformed JSON,
/// dev builds (no bundle version / no `LCBGitHubRepo`), or non-numeric tags
/// all mean "no hint", never an error. Follows the repo's `gh`-less
/// convention: a plain HTTPS GET, nothing installed, nothing shelled out.
public enum UpdateCheck {
    public static func releasesAPIURL(repo: String) -> URL? {
        URL(string: "https://api.github.com/repos/\(repo)/releases/latest")
    }

    /// Fallback download link when the payload carries no `html_url`.
    public static func downloadPageURL(repo: String) -> URL? {
        URL(string: "https://github.com/\(repo)/releases/latest")
    }

    public static func parseLatestRelease(_ data: Data) -> LatestRelease? {
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let payload = object as? [String: Any],
              let tag = payload["tag_name"] as? String
        else { return nil }
        let url = (payload["html_url"] as? String).flatMap(URL.init(string:))
        return LatestRelease(tag: tag, url: url)
    }

    /// Numeric dotted-version comparison after stripping a leading `v`.
    /// Anything non-numeric (a "dev" build, a `nightly` tag) compares false:
    /// when in doubt, stay silent.
    public static func isNewer(remoteTag: String, currentVersion: String) -> Bool {
        guard let remote = numericComponents(of: remoteTag),
              let current = numericComponents(of: currentVersion)
        else { return false }
        for index in 0..<max(remote.count, current.count) {
            let remotePart = index < remote.count ? remote[index] : 0
            let currentPart = index < current.count ? current[index] : 0
            if remotePart != currentPart { return remotePart > currentPart }
        }
        return false
    }

    /// Pure decision core, separated from the fetch so it is testable: given
    /// the running version, the configured repo, and a raw API payload,
    /// decide whether to hint and where the link goes.
    public static func hint(
        currentVersion: String?, repo: String?, releaseData: Data
    ) -> UpdateHint? {
        guard let repo, !repo.isEmpty,
              let currentVersion,
              let release = parseLatestRelease(releaseData),
              isNewer(remoteTag: release.tag, currentVersion: currentVersion),
              let url = release.url ?? downloadPageURL(repo: repo)
        else { return nil }
        var version = release.tag
        if version.hasPrefix("v") { version = String(version.dropFirst()) }
        return UpdateHint(version: version, url: url)
    }

    /// The launch-time check: one short GET against the releases API. Returns
    /// nil for dev builds (no version / no repo stamped in Info.plist) and on
    /// any network or payload failure.
    public static func check(
        currentVersion: String?, repo: String?, session: URLSession = .shared
    ) async -> UpdateHint? {
        guard let repo, !repo.isEmpty,
              let currentVersion, numericComponents(of: currentVersion) != nil,
              let apiURL = releasesAPIURL(repo: repo)
        else { return nil }
        var request = URLRequest(url: apiURL)
        request.timeoutInterval = 10
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        guard let (data, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse, http.statusCode == 200
        else { return nil }
        return hint(currentVersion: currentVersion, repo: repo, releaseData: data)
    }

    private static func numericComponents(of version: String) -> [Int]? {
        var text = version.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("v") { text = String(text.dropFirst()) }
        guard !text.isEmpty else { return nil }
        var components: [Int] = []
        for part in text.split(separator: ".", omittingEmptySubsequences: false) {
            guard let value = Int(part), value >= 0 else { return nil }
            components.append(value)
        }
        return components
    }
}
