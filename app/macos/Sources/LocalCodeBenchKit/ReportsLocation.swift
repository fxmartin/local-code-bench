import Foundation

/// Where benchmark results and the Epic-17 PDF reports live for a recorded
/// data location. Every Finder convenience (menu-bar/Dock recents, "Open
/// Results Folder", the report-download destination) resolves through here so
/// the app and the harness agree on one user-visible place (story 18.2-002).
public enum ReportsLocation {
    public static func resultsDirectory(
        for location: DataLocation,
        appSupportDirectory: URL = defaultAppSupportDirectory()
    ) -> URL {
        switch location {
        case .appSupportDefault: appSupportDirectory.appendingPathComponent("results")
        case .checkout(let root): root.appendingPathComponent("results")
        }
    }

    public static func reportsDirectory(
        for location: DataLocation,
        appSupportDirectory: URL = defaultAppSupportDirectory()
    ) -> URL {
        resultsDirectory(for: location, appSupportDirectory: appSupportDirectory)
            .appendingPathComponent("reports")
    }
}
