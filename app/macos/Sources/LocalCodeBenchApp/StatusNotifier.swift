import AppKit
import LocalCodeBenchKit
import UserNotifications

/// Posts native notifications for status events and routes clicks back into
/// the app. Notifications only fire while the app is in the background — in
/// the foreground the dashboard itself shows the outcome.
///
/// `UNUserNotificationCenter` requires a real bundle identifier; a dev build
/// run via `swift run` has none, so the notifier degrades to a no-op there
/// instead of crashing (the menu-bar status still works).
@MainActor
final class StatusNotifier: NSObject, UNUserNotificationCenterDelegate {
    /// Called with the dashboard section a clicked notification targets.
    var onOpenSection: ((String) -> Void)?

    private static let downloadCategory = "report-download"
    private static let revealAction = "reveal-in-finder"

    private let available = Bundle.main.bundleIdentifier != nil
    private var authorizationRequested = false

    func requestAuthorization() {
        guard available, !authorizationRequested else { return }
        authorizationRequested = true
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        // Report-download notifications carry an explicit "Reveal in Finder"
        // action button (story 18.2-002); a plain click reveals too.
        let reveal = UNNotificationAction(
            identifier: Self.revealAction, title: DownloadNotification.revealActionTitle)
        center.setNotificationCategories([
            UNNotificationCategory(
                identifier: Self.downloadCategory, actions: [reveal],
                intentIdentifiers: [])
        ])
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func post(_ content: NotificationContent) {
        guard available, !NSApp.isActive else { return }
        let notification = UNMutableNotificationContent()
        notification.title = content.title
        notification.body = content.body
        notification.userInfo = ["section": content.section]
        let request = UNNotificationRequest(
            identifier: UUID().uuidString, content: notification, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }

    /// A finished report download. Unlike status events this also fires while
    /// the app is frontmost — with no save panel, the notification is how the
    /// user learns where the PDF landed. Returns false when notifications are
    /// unavailable (unbundled dev build) so the caller can fall back.
    func postDownloadFinished(at fileURL: URL) -> Bool {
        guard available else { return false }
        let content = DownloadNotification.finished(filename: fileURL.lastPathComponent)
        let notification = UNMutableNotificationContent()
        notification.title = content.title
        notification.body = content.body
        notification.categoryIdentifier = Self.downloadCategory
        notification.userInfo = ["revealPath": fileURL.path]
        let request = UNNotificationRequest(
            identifier: UUID().uuidString, content: notification, trigger: nil)
        UNUserNotificationCenter.current().add(request)
        return true
    }

    /// Foreground delivery: download notifications must still show as a
    /// banner while the user is in the app.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler:
            @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let userInfo = response.notification.request.content.userInfo
        if let path = userInfo["revealPath"] as? String {
            Task { @MainActor in
                NSWorkspace.shared.activateFileViewerSelecting(
                    [URL(fileURLWithPath: path)])
            }
        } else if let section = userInfo["section"] as? String {
            Task { @MainActor in
                self.onOpenSection?(section)
            }
        }
        completionHandler()
    }
}
