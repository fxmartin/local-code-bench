import Foundation

/// Detects a login-item launch from the app's opening Apple event so the app
/// can start quietly in the menu bar instead of splashing the dashboard
/// window across login (story 18.2-002). Pure code mapping — the AppKit side
/// feeds it the current event's four-char codes.
public enum LoginItemLaunch {
    /// 'aevt' — kCoreEventClass
    public static let openApplicationClass: UInt32 = 0x6165_7674
    /// 'oapp' — kAEOpenApplication
    public static let openApplicationID: UInt32 = 0x6F61_7070
    /// 'prdt' — keyAEPropData, the open-event parameter carrying the launch reason
    public static let propDataKeyword: UInt32 = 0x7072_6474
    /// 'lgit' — keyAELaunchedAsLogInItem
    public static let launchedAsLoginItem: UInt32 = 0x6C67_6974

    public static func isLoginItemLaunch(
        eventClass: UInt32?, eventID: UInt32?, propData: UInt32?
    ) -> Bool {
        eventClass == openApplicationClass
            && eventID == openApplicationID
            && propData == launchedAsLoginItem
    }
}
