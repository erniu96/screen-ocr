#!/usr/bin/env swift
import CryptoKit
import Foundation

// Local packaging only: third-party runtime assets never become repository files.
let fileManager = FileManager.default
let root = URL(fileURLWithPath: #filePath).standardizedFileURL.deletingLastPathComponent().deletingLastPathComponent()
let package = root.appendingPathComponent("apps/macos", isDirectory: true)
let appName = "Screen OCR.app"
let bundleID = "dev.screenocr.menubar"

struct BuildError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

struct Asset: Decodable {
    let path: String
    let category: String
    let bytes: Int
    let sha256: String
}

struct Baseline: Decodable {
    let format_version: Int
    let source_version: String
    let source_build: String
    let architecture: String
    let source_url: String
    let source_dmg_sha256: String
    let files: [Asset]
}

struct Manifest: Encodable {
    let format_version = 1
    let engine = "Third-party native macOS OCR"
    let source_version: String
    let source_build: String
    let architecture: String
    let source_url: String
    let source_dmg_sha256: String
    // Source hashes identify the verified upstream files; signing changes dylib bytes.
    let source_sha256: [String: String]
    let sha256: [String: String]
}

@discardableResult
func run(_ executable: String, _ arguments: [String], capture: Bool = false,
         allowedStatuses: Set<Int32> = [0]) throws -> (status: Int32, output: String) {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    let pipe = capture ? Pipe() : nil
    if let pipe { process.standardOutput = pipe }
    try process.run()
    // Drain before wait so captured diagnostic output cannot fill the pipe.
    let data = pipe?.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    let output = data.map { String(decoding: $0, as: UTF8.self) } ?? ""
    guard allowedStatuses.contains(process.terminationStatus) else {
        throw BuildError("Command failed (\(process.terminationStatus)): \(executable) \(arguments.joined(separator: " "))\n\(output)")
    }
    return (process.terminationStatus, output)
}

func digest(_ url: URL) throws -> String {
    let handle = try FileHandle(forReadingFrom: url)
    defer { try? handle.close() }
    var hash = SHA256()
    while let chunk = try handle.read(upToCount: 1024 * 1024), !chunk.isEmpty {
        hash.update(data: chunk)
    }
    return hash.finalize().map { String(format: "%02x", $0) }.joined()
}

func verifyAssets(_ directory: URL, baseline: Baseline) throws {
    guard baseline.format_version == 1, baseline.architecture == "arm64", !baseline.files.isEmpty else {
        throw BuildError("Unsupported runtime baseline")
    }
    var paths = Set<String>()
    for asset in baseline.files {
        let parts = asset.path.split(separator: "/", omittingEmptySubsequences: false)
        guard !asset.path.hasPrefix("/"), !parts.contains(".."), !parts.contains(""),
              paths.insert(asset.path).inserted else { throw BuildError("Invalid baseline asset path") }
        let file = directory.appendingPathComponent(asset.path)
        let values: URLResourceValues
        do { values = try file.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey, .isSymbolicLinkKey]) }
        catch { throw BuildError("Cannot read runtime asset \(file.path): \(error.localizedDescription)") }
        guard values.isRegularFile == true, values.isSymbolicLink != true,
              values.fileSize == asset.bytes, try digest(file) == asset.sha256 else {
            throw BuildError("Runtime checksum mismatch: \(asset.path). Use the verified source files; do not mix versions.")
        }
    }
}

func ensureReplaceable(_ destination: URL) throws {
    guard (try? fileManager.destinationOfSymbolicLink(atPath: destination.path)) == nil else {
        throw BuildError("Refusing to replace a symlink: \(destination.path)")
    }
    guard fileManager.fileExists(atPath: destination.path) else { return }
    let data = try Data(contentsOf: destination.appendingPathComponent("Contents/Info.plist"))
    let plist = try PropertyListSerialization.propertyList(from: data, format: nil)
    guard let info = plist as? [String: Any], info["CFBundleIdentifier"] as? String == bundleID else {
        throw BuildError("Another application already occupies \(destination.path)")
    }
    let path = destination.appendingPathComponent("Contents/MacOS/ScreenOCR").path
    let pattern = "^" + NSRegularExpression.escapedPattern(for: path) + "([[:space:]]|$)"
    let process = try run("/usr/bin/pgrep", ["-f", pattern], capture: true, allowedStatuses: [0, 1])
    guard process.status == 1 else { throw BuildError("Quit Screen OCR before replacing \(destination.path)") }
}

func sign(_ url: URL, identifier: String) throws {
    try run("/usr/bin/codesign", ["--force", "--sign", "-", "--timestamp=none", "--identifier", identifier, url.path])
    try run("/usr/bin/codesign", ["--verify", "--strict", url.path])
}

func replaceBundle(_ source: URL, _ destination: URL) throws {
    try ensureReplaceable(destination)
    let parent = destination.deletingLastPathComponent()
    try fileManager.createDirectory(at: parent, withIntermediateDirectories: true)
    let work = parent.appendingPathComponent(".screen-ocr-install-\(UUID().uuidString)", isDirectory: true)
    try fileManager.createDirectory(at: work, withIntermediateDirectories: false,
                                    attributes: [.posixPermissions: 0o700])
    var removeWork = true
    defer { if removeWork { try? fileManager.removeItem(at: work) } }
    let stage = work.appendingPathComponent(appName, isDirectory: true)
    try fileManager.copyItem(at: source, to: stage)
    try run("/usr/bin/codesign", ["--verify", "--strict", stage.path])
    // Check again immediately before replacement, after copying the large runtime.
    try ensureReplaceable(destination)
    let previous = work.appendingPathComponent("previous.app", isDirectory: true)
    let hadPrevious = fileManager.fileExists(atPath: destination.path)
    if hadPrevious { try fileManager.moveItem(at: destination, to: previous) }
    do { try fileManager.moveItem(at: stage, to: destination) }
    catch {
        if hadPrevious {
            do { try fileManager.moveItem(at: previous, to: destination) }
            catch {
                removeWork = false
                throw BuildError("Installation and rollback failed; the previous App is preserved at \(previous.path)")
            }
        }
        throw error
    }
}

func build(runtime: URL) throws -> URL {
    let output = root.appendingPathComponent("dist/\(appName)", isDirectory: true)
    try ensureReplaceable(output)
    let baseline = try JSONDecoder().decode(Baseline.self, from: Data(contentsOf: root.appendingPathComponent("src/screen_ocr/data/runtime-baseline.json")))
    try verifyAssets(runtime, baseline: baseline)
    let command = ["swift", "build", "--package-path", package.path, "--configuration", "release", "--arch", "arm64"]
    try run("/usr/bin/xcrun", command)
    let path = try run("/usr/bin/xcrun", command + ["--show-bin-path"], capture: true).output.trimmingCharacters(in: .whitespacesAndNewlines)
    guard path.hasPrefix("/"), !path.contains("\n") else { throw BuildError("Swift returned an invalid binary directory") }
    let binaries = URL(fileURLWithPath: path, isDirectory: true)
    let parent = output.deletingLastPathComponent()
    try fileManager.createDirectory(at: parent, withIntermediateDirectories: true)
    let work = parent.appendingPathComponent(".screen-ocr-build-\(UUID().uuidString)", isDirectory: true)
    try fileManager.createDirectory(at: work, withIntermediateDirectories: false,
                                    attributes: [.posixPermissions: 0o700])
    defer { try? fileManager.removeItem(at: work) }
    let stage = work.appendingPathComponent(appName, isDirectory: true)
    let contents = stage.appendingPathComponent("Contents", isDirectory: true)
    let bundledRuntime = contents.appendingPathComponent("Resources/Runtime", isDirectory: true)
    for directory in ["MacOS", "Resources/Runtime/bin", "Resources/Runtime/Frameworks", "Resources/Runtime/Resources"] {
        try fileManager.createDirectory(at: contents.appendingPathComponent(directory), withIntermediateDirectories: true)
    }
    for executable in ["ScreenOCR", "ScreenOCRWorker"] {
        let target = contents.appendingPathComponent("MacOS/\(executable)")
        try fileManager.copyItem(at: binaries.appendingPathComponent(executable), to: target)
        try sign(target, identifier: "\(bundleID).\(executable)")
    }
    try fileManager.copyItem(at: package.appendingPathComponent("Packaging/Info.plist"), to: contents.appendingPathComponent("Info.plist"))
    try fileManager.copyItem(at: root.appendingPathComponent("LICENSE"), to: contents.appendingPathComponent("Resources/LICENSE"))
    try fileManager.copyItem(at: root.appendingPathComponent("docs/THIRD_PARTY_NOTICES.md"), to: contents.appendingPathComponent("Resources/THIRD_PARTY_NOTICES.md"))
    for asset in baseline.files {
        try fileManager.copyItem(at: runtime.appendingPathComponent(asset.path), to: bundledRuntime.appendingPathComponent(asset.path))
    }
    try verifyAssets(bundledRuntime, baseline: baseline)
    let helper = bundledRuntime.appendingPathComponent("bin/screen-ocr-engine")
    try run("/usr/bin/xcrun", ["clang", "-arch", "arm64", "-mmacosx-version-min=13.0", "-O2", "-Wall", "-Wextra", "-Werror",
                               root.appendingPathComponent("native/ocr_helper.c").path,
                               "-Wl,-rpath,@loader_path/../Frameworks", "-o", helper.path])
    for asset in baseline.files where asset.category == "library" {
        try sign(bundledRuntime.appendingPathComponent(asset.path), identifier: "\(bundleID).\(URL(fileURLWithPath: asset.path).lastPathComponent)")
    }
    try sign(helper, identifier: "\(bundleID).engine")
    var packagedHashes: [String: String] = [:]
    for path in baseline.files.map(\.path) + ["bin/screen-ocr-engine"] {
        packagedHashes[path] = try digest(bundledRuntime.appendingPathComponent(path))
    }
    let manifest = Manifest(source_version: baseline.source_version, source_build: baseline.source_build,
                            architecture: baseline.architecture, source_url: baseline.source_url,
                            source_dmg_sha256: baseline.source_dmg_sha256,
                            source_sha256: Dictionary(uniqueKeysWithValues: baseline.files.map { ($0.path, $0.sha256) }),
                            sha256: packagedHashes)
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    try encoder.encode(manifest).write(to: bundledRuntime.appendingPathComponent("runtime.json"))
    let iconset = work.appendingPathComponent("AppIcon.iconset", isDirectory: true)
    try run("/usr/bin/xcrun", ["swift", package.appendingPathComponent("Packaging/MakeIcon.swift").path, iconset.path])
    try run("/usr/bin/iconutil", ["-c", "icns", iconset.path, "-o", contents.appendingPathComponent("Resources/AppIcon.icns").path])
    try sign(stage, identifier: bundleID)
    try replaceBundle(stage, output)
    return output
}

func main() throws {
    #if !os(macOS) || !arch(arm64)
    throw BuildError("Requires macOS on Apple Silicon with Swift 6 or newer")
    #endif
    var runtime = root.appendingPathComponent(".runtime", isDirectory: true)
    var install = false
    var open = false
    var arguments = Array(CommandLine.arguments.dropFirst()).makeIterator()
    while let argument = arguments.next() {
        switch argument {
        case "--runtime":
            guard let path = arguments.next(), !path.isEmpty else { throw BuildError("--runtime requires a path") }
            runtime = URL(fileURLWithPath: path, isDirectory: true).standardizedFileURL
        case "--install": install = true
        case "--open": open = true
        case "--help", "-h":
            print("Usage: swift scripts/build_macos_app.swift [--runtime PATH] [--install] [--open]")
            return
        default: throw BuildError("Unknown option: \(argument)")
        }
    }
    if install { try ensureReplaceable(fileManager.homeDirectoryForCurrentUser.appendingPathComponent("Applications/\(appName)")) }
    var app = try build(runtime: runtime)
    if install {
        let destination = fileManager.homeDirectoryForCurrentUser.appendingPathComponent("Applications/\(appName)")
        try replaceBundle(app, destination)
        app = destination
    }
    print("Built standalone Screen OCR: \(app.path)")
    if open { try run("/usr/bin/open", [app.path]) }
}

do { try main() }
catch {
    FileHandle.standardError.write(Data("screen-ocr app build: \(error.localizedDescription)\n".utf8))
    exit(1)
}
