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

    private let available = Bundle.main.bundleIdentifier != nil
    private var authorizationRequested = false

    func requestAuthorization() {
        guard available, !authorizationRequested else { return }
        authorizationRequested = true
        let center = UNUserNotificationCenter.current()
        center.delegate = self
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

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let section =
            response.notification.request.content.userInfo["section"] as? String
        if let section {
            Task { @MainActor in
                self.onOpenSection?(section)
            }
        }
        completionHandler()
    }
}
