import Foundation

/// The relocatable CPython embedded in the app bundle
/// (`Contents/Resources/python`, assembled by `scripts/build-macos-app.sh`
/// from python-build-standalone plus the harness wheel). Absent in dev builds
/// (`swift run`), where the PATH / uv launch paths apply instead.
public struct BundledRuntime: Equatable, Sendable {
    public let python: URL

    public init(python: URL) {
        self.python = python
    }

    public static func locate(
        resourcesDirectory: URL?, fileManager: FileManager = .default
    ) -> BundledRuntime? {
        guard let resourcesDirectory else { return nil }
        let python = resourcesDirectory.appendingPathComponent("python/bin/python3")
        guard fileManager.isExecutableFile(atPath: python.path) else { return nil }
        return BundledRuntime(python: python)
    }
}
