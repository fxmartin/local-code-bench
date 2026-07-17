import Foundation

/// Last-N-lines extraction for the failure view: when startup fails the window
/// shows the tail of the captured service log instead of a blank WKWebView.
public enum LogTail {
    public static func tail(_ text: String, lines: Int) -> String {
        guard lines > 0, !text.isEmpty else { return "" }
        let all = text.hasSuffix("\n") ? String(text.dropLast()) : text
        let split = all.components(separatedBy: "\n")
        return split.suffix(lines).joined(separator: "\n")
    }

    /// Reads at most `maxBytes` from the end of the file so tailing a large log
    /// stays cheap; a partial first line from the byte cut is acceptable.
    public static func tail(fileAt url: URL, lines: Int, maxBytes: Int = 65_536) -> String {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return "" }
        defer { try? handle.close() }
        guard let size = try? handle.seekToEnd() else { return "" }
        let start = size > UInt64(maxBytes) ? size - UInt64(maxBytes) : 0
        guard (try? handle.seek(toOffset: start)) != nil,
              let data = try? handle.readToEnd(),
              let text = String(data: data, encoding: .utf8)
        else { return "" }
        return tail(text, lines: lines)
    }
}
