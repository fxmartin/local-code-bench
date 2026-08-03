import Foundation

/// Seeds the app-support data directory from bundled default configs.
///
/// The bundled dashboard runs with the chosen data directory as cwd, so the
/// app-support default needs a usable `configs/` tree before `bench dashboard`
/// starts. Existing files are preserved so user edits made through Settings are
/// not overwritten by a later app launch.
public enum DefaultConfigSeeder {
    public static func seed(
        appSupportDirectory: URL,
        resourcesDirectory: URL?,
        fileManager: FileManager = .default
    ) throws {
        guard let source = resourcesDirectory?.appendingPathComponent("default-configs")
        else { return }

        var isDir: ObjCBool = false
        guard fileManager.fileExists(atPath: source.path, isDirectory: &isDir),
              isDir.boolValue
        else { return }

        let destination = appSupportDirectory.appendingPathComponent("configs")
        try fileManager.createDirectory(at: destination, withIntermediateDirectories: true)
        try copyMissingItems(from: source, to: destination, fileManager: fileManager)
    }

    private static func copyMissingItems(
        from source: URL,
        to destination: URL,
        fileManager: FileManager
    ) throws {
        let items = try fileManager.contentsOfDirectory(
            at: source,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles])

        for item in items {
            let target = destination.appendingPathComponent(item.lastPathComponent)
            var isDir: ObjCBool = false
            if fileManager.fileExists(atPath: target.path, isDirectory: &isDir) {
                if isDir.boolValue {
                    try copyMissingItems(from: item, to: target, fileManager: fileManager)
                }
                continue
            }
            try fileManager.copyItem(at: item, to: target)
        }
    }
}
