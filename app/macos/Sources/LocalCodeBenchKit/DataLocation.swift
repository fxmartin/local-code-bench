import Foundation

/// Where the app keeps benchmark data: a private app-support directory, or an
/// existing `local-code-bench` checkout so configs/results are shared with the
/// CLI. Recorded once by the first-run panel.
public enum DataLocation: Equatable, Sendable, Codable {
    case appSupportDefault
    case checkout(URL)
}

/// Persists the first-run choice. `recorded == nil` means first run.
public struct DataLocationStore {
    public static let key = "dataLocation"

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public var recorded: DataLocation? {
        guard let data = defaults.data(forKey: Self.key) else { return nil }
        return try? JSONDecoder().decode(DataLocation.self, from: data)
    }

    public var isFirstRun: Bool { recorded == nil }

    public func record(_ location: DataLocation) {
        guard let data = try? JSONEncoder().encode(location) else { return }
        defaults.set(data, forKey: Self.key)
    }
}

/// Sanity check that a user-picked directory really is a local-code-bench
/// checkout before recording it: it must have both `configs/` and a
/// `pyproject.toml`, the two things the dashboard service reads from cwd.
public enum CheckoutValidation {
    public static func isBenchCheckout(
        _ directory: URL, fileManager: FileManager = .default
    ) -> Bool {
        var isDir: ObjCBool = false
        let configs = directory.appendingPathComponent("configs")
        guard fileManager.fileExists(atPath: configs.path, isDirectory: &isDir),
              isDir.boolValue
        else { return false }
        return fileManager.fileExists(
            atPath: directory.appendingPathComponent("pyproject.toml").path)
    }
}

/// Default data directory for the app-support choice.
public func defaultAppSupportDirectory(fileManager: FileManager = .default) -> URL {
    let base = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
        ?? fileManager.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support")
    return base.appendingPathComponent("LocalCodeBench")
}
