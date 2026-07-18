import Foundation

/// The most recent Epic-17 PDF reports in `results/reports/`, newest first —
/// the small recents list behind the menu-bar and Dock menus (story 18.2-002).
public enum RecentReports {
    public static let defaultLimit = 5

    public static func list(
        in directory: URL, limit: Int = defaultLimit, fileManager: FileManager = .default
    ) -> [URL] {
        guard let entries = try? fileManager.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [.contentModificationDateKey, .isRegularFileKey],
            options: [.skipsHiddenFiles])
        else { return [] }
        let reports = entries.compactMap { url -> (url: URL, modified: Date)? in
            guard url.pathExtension.lowercased() == "pdf",
                  let values = try? url.resourceValues(
                    forKeys: [.contentModificationDateKey, .isRegularFileKey]),
                  values.isRegularFile == true
            else { return nil }
            return (url, values.contentModificationDate ?? .distantPast)
        }
        return reports.sorted { $0.modified > $1.modified }
            .prefix(max(0, limit)).map(\.url)
    }
}
