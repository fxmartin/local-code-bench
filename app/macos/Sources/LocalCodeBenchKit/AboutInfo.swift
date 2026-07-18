import Foundation

/// Versions shown in the about panel (Story 18.3-001): the app version comes
/// from `CFBundleShortVersionString` (stamped from `pyproject.toml` by
/// `scripts/build-macos-app.sh`), the harness version from the
/// `Contents/Resources/harness-version` file the build script writes after
/// installing the wheel. Dev builds (`swift run`) have neither.
public struct AboutInfo: Equatable, Sendable {
    public let appVersion: String
    public let harnessVersion: String

    public init(appVersion: String, harnessVersion: String) {
        self.appVersion = appVersion
        self.harnessVersion = harnessVersion
    }

    public static func resolve(
        bundleShortVersion: String?, bundledHarnessVersion: String?
    ) -> AboutInfo {
        AboutInfo(
            appVersion: nonEmpty(bundleShortVersion) ?? "dev",
            harnessVersion: nonEmpty(bundledHarnessVersion) ?? "unbundled"
        )
    }

    /// Reads the harness version recorded by the build script; nil in dev
    /// builds or when the file is missing/empty.
    public static func bundledHarnessVersion(
        resourcesDirectory: URL?, fileManager: FileManager = .default
    ) -> String? {
        guard let resourcesDirectory else { return nil }
        let file = resourcesDirectory.appendingPathComponent("harness-version")
        guard let data = fileManager.contents(atPath: file.path),
              let text = String(data: data, encoding: .utf8)
        else { return nil }
        return nonEmpty(text)
    }

    private static func nonEmpty(_ value: String?) -> String? {
        guard let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty
        else { return nil }
        return trimmed
    }
}
