import Foundation

/// Fully-resolved command for launching `bench dashboard`, derived from the
/// recorded data location. Pure value type so the argv/cwd rules are testable
/// without spawning anything.
public struct ServiceLaunchPlan: Equatable, Sendable {
    public let executable: String
    public let arguments: [String]
    public let workingDirectory: URL
    public let logFile: URL

    public init(
        executable: String,
        arguments: [String],
        workingDirectory: URL,
        logFile: URL
    ) {
        self.executable = executable
        self.arguments = arguments
        self.workingDirectory = workingDirectory
        self.logFile = logFile
    }

    public static func plan(
        for location: DataLocation,
        host: String,
        port: Int,
        appSupportDirectory: URL? = nil
    ) -> ServiceLaunchPlan {
        let appSupport = appSupportDirectory ?? defaultAppSupportDirectory()
        let dashboardArgs = ["dashboard", "--host", host, "--port", String(port)]
        switch location {
        case .checkout(let checkout):
            // Inside a checkout, uv resolves the project env so the service
            // matches whatever the CLI runs there.
            return ServiceLaunchPlan(
                executable: "/usr/bin/env",
                arguments: ["uv", "run", "bench"] + dashboardArgs,
                workingDirectory: checkout,
                logFile: appSupport.appendingPathComponent("dashboard-service.log"))
        case .appSupportDefault:
            // No checkout: rely on a user-installed `bench` on PATH (e.g.
            // `uv tool install local-code-bench`), cwd'd into app support so
            // relative configs/results paths land there.
            return ServiceLaunchPlan(
                executable: "/usr/bin/env",
                arguments: ["bench"] + dashboardArgs,
                workingDirectory: appSupport,
                logFile: appSupport.appendingPathComponent("dashboard-service.log"))
        }
    }
}
