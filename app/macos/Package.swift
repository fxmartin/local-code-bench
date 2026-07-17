// swift-tools-version: 6.0
// LocalCodeBench macOS shell (Story 18.1-001).
//
// Three targets:
//   - LocalCodeBenchKit    pure-Foundation logic (startup state machine, data
//                          location, log tail, navigation policy, launch plan)
//   - LocalCodeBench       the SwiftUI app shell hosting the dashboard WKWebView
//   - LocalCodeBenchChecks assertion-based test runner for the kit. It exists
//                          because `swift test` needs Xcode's XCTest/Testing
//                          runtime, which the benchmark machine (Command Line
//                          Tools only) does not ship. Run: `swift run LocalCodeBenchChecks`
import PackageDescription

let package = Package(
    name: "LocalCodeBench",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "LocalCodeBenchKit"),
        .executableTarget(
            name: "LocalCodeBench",
            dependencies: ["LocalCodeBenchKit"],
            path: "Sources/LocalCodeBenchApp"
        ),
        .executableTarget(
            name: "LocalCodeBenchChecks",
            dependencies: ["LocalCodeBenchKit"]
        ),
    ]
)
