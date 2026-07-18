// Checks for the macOS conveniences (Story 18.2-002): reports location,
// the recents list, report-download routing, download notification content,
// and login-item launch detection.
import Foundation
import LocalCodeBenchKit

// MARK: - ReportsLocation

@MainActor func checkReportsLocation() {
    let checkout = URL(fileURLWithPath: "/Users/fx/dev/local-code-bench")
    let appSupport = URL(fileURLWithPath: "/tmp/lcb-app-support")

    expectEqual(
        ReportsLocation.resultsDirectory(for: .checkout(checkout), appSupportDirectory: appSupport),
        checkout.appendingPathComponent("results"),
        "checkout results dir is <checkout>/results")
    expectEqual(
        ReportsLocation.reportsDirectory(for: .checkout(checkout), appSupportDirectory: appSupport),
        checkout.appendingPathComponent("results/reports"),
        "checkout reports dir is <checkout>/results/reports")
    expectEqual(
        ReportsLocation.resultsDirectory(for: .appSupportDefault, appSupportDirectory: appSupport),
        appSupport.appendingPathComponent("results"),
        "app-support results dir is <app support>/results")
    expectEqual(
        ReportsLocation.reportsDirectory(for: .appSupportDefault, appSupportDirectory: appSupport),
        appSupport.appendingPathComponent("results/reports"),
        "app-support reports dir is <app support>/results/reports")
}

// MARK: - RecentReports

@MainActor func checkRecentReports() throws {
    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }

    expectEqual(
        RecentReports.list(in: dir.appendingPathComponent("missing")), [],
        "missing reports dir lists empty")
    expectEqual(RecentReports.list(in: dir), [], "empty reports dir lists empty")

    func write(_ name: String, mtime: TimeInterval) throws {
        let url = dir.appendingPathComponent(name)
        try "x".write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes(
            [.modificationDate: Date(timeIntervalSince1970: mtime)], ofItemAtPath: url.path)
    }
    try write("old.pdf", mtime: 100)
    try write("Mid.PDF", mtime: 200)
    try write("new.pdf", mtime: 300)
    try write("notes.txt", mtime: 400)
    // A directory named like a report must not be listed.
    try FileManager.default.createDirectory(
        at: dir.appendingPathComponent("folder.pdf"), withIntermediateDirectories: true)

    expectEqual(
        RecentReports.list(in: dir).map(\.lastPathComponent),
        ["new.pdf", "Mid.PDF", "old.pdf"],
        "only PDF files are listed, newest first")
    expectEqual(
        RecentReports.list(in: dir, limit: 2).map(\.lastPathComponent),
        ["new.pdf", "Mid.PDF"],
        "limit caps the recents list")
    expectEqual(RecentReports.list(in: dir, limit: 0), [], "zero limit lists nothing")
    expectEqual(RecentReports.list(in: dir, limit: -3), [], "negative limit lists nothing")
}

// MARK: - ReportDownload

@MainActor func checkReportDownload() throws {
    expect(ReportDownload.isPDF(filename: "compare.pdf"), "lowercase .pdf is a report")
    expect(ReportDownload.isPDF(filename: "COMPARE.PDF"), "extension match is case-insensitive")
    expect(!ReportDownload.isPDF(filename: "results.csv"), "non-PDF is not a report")
    expect(!ReportDownload.isPDF(filename: "pdf"), "extensionless name is not a report")

    expectEqual(
        ReportDownload.sanitizedFilename("compare.pdf"), "compare.pdf",
        "plain filename passes through")
    expectEqual(
        ReportDownload.sanitizedFilename("../../etc/evil.pdf"), "evil.pdf",
        "path components cannot escape the reports directory")
    expectEqual(
        ReportDownload.sanitizedFilename(""), "report.pdf", "empty name falls back")
    expectEqual(
        ReportDownload.sanitizedFilename("/"), "report.pdf", "bare slash falls back")
    expectEqual(
        ReportDownload.sanitizedFilename("a/.."), "report.pdf", "dot-dot falls back")
    expectEqual(
        ReportDownload.sanitizedFilename("."), "report.pdf", "bare dot falls back")

    let dir = try makeTempDir()
    defer { try? FileManager.default.removeItem(at: dir) }

    let first = ReportDownload.destination(suggestedFilename: "compare.pdf", in: dir)
    expectEqual(first.lastPathComponent, "compare.pdf", "free name is used as-is")
    expectEqual(
        first.deletingLastPathComponent().path, dir.path,
        "destination is inside the reports dir")

    try "x".write(to: first, atomically: true, encoding: .utf8)
    let second = ReportDownload.destination(suggestedFilename: "compare.pdf", in: dir)
    expectEqual(
        second.lastPathComponent, "compare-2.pdf",
        "collision appends a counter before the extension")

    try "x".write(to: second, atomically: true, encoding: .utf8)
    expectEqual(
        ReportDownload.destination(suggestedFilename: "compare.pdf", in: dir).lastPathComponent,
        "compare-3.pdf",
        "counter keeps climbing past further collisions")

    // Extensionless names must still get collision counters, without a
    // trailing dot.
    let bare = dir.appendingPathComponent("summary")
    try "x".write(to: bare, atomically: true, encoding: .utf8)
    expectEqual(
        ReportDownload.destination(suggestedFilename: "summary", in: dir).lastPathComponent,
        "summary-2",
        "extensionless collision appends a bare counter")
}

// MARK: - DownloadNotification

@MainActor func checkDownloadNotification() {
    let content = DownloadNotification.finished(filename: "compare.pdf")
    expectEqual(content.title, "Report downloaded", "download notification title")
    expect(content.body.contains("compare.pdf"), "download notification body names the file")
    expectEqual(
        DownloadNotification.revealActionTitle, "Reveal in Finder",
        "the notification action is the acceptance-visible Reveal in Finder")
}

// MARK: - LoginItemLaunch

@MainActor func checkLoginItemLaunch() {
    expect(
        LoginItemLaunch.isLoginItemLaunch(
            eventClass: LoginItemLaunch.openApplicationClass,
            eventID: LoginItemLaunch.openApplicationID,
            propData: LoginItemLaunch.launchedAsLoginItem),
        "open-app event flagged 'lgit' is a login-item launch")
    expect(
        !LoginItemLaunch.isLoginItemLaunch(
            eventClass: LoginItemLaunch.openApplicationClass,
            eventID: LoginItemLaunch.openApplicationID,
            propData: nil),
        "open-app event without the flag is a normal launch")
    expect(
        !LoginItemLaunch.isLoginItemLaunch(
            eventClass: LoginItemLaunch.openApplicationClass,
            eventID: 0x6F646F63, // 'odoc' — open-document, not open-app
            propData: LoginItemLaunch.launchedAsLoginItem),
        "non-open-app event is a normal launch even with the flag")
    expect(
        !LoginItemLaunch.isLoginItemLaunch(eventClass: nil, eventID: nil, propData: nil),
        "no opening apple event is a normal launch")
}
