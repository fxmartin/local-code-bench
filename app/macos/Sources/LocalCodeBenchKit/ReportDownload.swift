import Foundation

/// Routing for dashboard-triggered downloads (story 18.2-002): an Epic-17
/// PDF report lands in the user-visible reports folder under a
/// collision-free name instead of going through a save panel.
public enum ReportDownload {
    public static let fallbackFilename = "report.pdf"

    public static func isPDF(filename: String) -> Bool {
        (filename as NSString).pathExtension.lowercased() == "pdf"
    }

    /// Suggested names come from the page/server and are untrusted: keep only
    /// the last path component so a crafted name cannot escape the reports
    /// directory, and fall back when nothing usable remains.
    public static func sanitizedFilename(_ suggested: String) -> String {
        let name = (suggested as NSString).lastPathComponent
        let unusable = ["", "/", ".", ".."]
        return unusable.contains(name) ? fallbackFilename : name
    }

    /// A destination in `directory` that does not collide with an existing
    /// file: `name.pdf`, then `name-2.pdf`, `name-3.pdf`, …
    public static func destination(
        suggestedFilename: String, in directory: URL, fileManager: FileManager = .default
    ) -> URL {
        let name = sanitizedFilename(suggestedFilename)
        let stem = (name as NSString).deletingPathExtension
        let ext = (name as NSString).pathExtension
        var candidate = directory.appendingPathComponent(name)
        var counter = 2
        while fileManager.fileExists(atPath: candidate.path) {
            let numbered = ext.isEmpty ? "\(stem)-\(counter)" : "\(stem)-\(counter).\(ext)"
            candidate = directory.appendingPathComponent(numbered)
            counter += 1
        }
        return candidate
    }
}

/// What the download-finished notification says; its action button is the
/// acceptance-visible "Reveal in Finder".
public struct DownloadNotification: Equatable, Sendable {
    public static let revealActionTitle = "Reveal in Finder"

    public let title: String
    public let body: String

    public static func finished(filename: String) -> DownloadNotification {
        DownloadNotification(
            title: "Report downloaded",
            body: "\(filename) saved to the reports folder")
    }
}
