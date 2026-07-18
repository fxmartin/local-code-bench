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

    /// Where `dashboard_lifecycle` writes the service's PID/state file: the
    /// CLI default `.runtime/dashboard.json` resolved against the plan's cwd.
    public var stateFile: URL {
        workingDirectory.appendingPathComponent(".runtime/dashboard.json")
    }

    public static func plan(
        for location: DataLocation,
        host: String,
        port: Int,
        runtime: BundledRuntime? = nil,
        appSupportDirectory: URL? = nil
    ) -> ServiceLaunchPlan {
        let appSupport = appSupportDirectory ?? defaultAppSupportDirectory()
        let logFile = appSupport.appendingPathComponent("dashboard-service.log")
        // --exit-with-parent: an app-launched service must die with the app
        // even after a force-quit, so it can never be left orphaned.
        let dashboardArgs = [
            "dashboard", "--host", host, "--port", String(port), "--exit-with-parent",
        ]
        let workingDirectory: URL
        switch location {
        case .checkout(let checkout): workingDirectory = checkout
        case .appSupportDefault: workingDirectory = appSupport
        }

        if let runtime {
            // Bundled relocatable CPython: console-script shims carry absolute
            // build-time shebangs, so the CLI is launched as a module instead.
            return ServiceLaunchPlan(
                executable: runtime.python.path,
                arguments: ["-m", "local_code_bench"] + dashboardArgs,
                workingDirectory: workingDirectory,
                logFile: logFile)
        }

        switch location {
        case .checkout:
            // Inside a checkout, uv resolves the project env so the service
            // matches whatever the CLI runs there.
            return ServiceLaunchPlan(
                executable: "/usr/bin/env",
                arguments: ["uv", "run", "bench"] + dashboardArgs,
                workingDirectory: workingDirectory,
                logFile: logFile)
        case .appSupportDefault:
            // No checkout: rely on a user-installed `bench` on PATH (e.g.
            // `uv tool install local-code-bench`), cwd'd into app support so
            // relative configs/results paths land there.
            return ServiceLaunchPlan(
                executable: "/usr/bin/env",
                arguments: ["bench"] + dashboardArgs,
                workingDirectory: workingDirectory,
                logFile: logFile)
        }
    }
}
