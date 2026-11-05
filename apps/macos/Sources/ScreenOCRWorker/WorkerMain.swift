import CMojoBridge
import Darwin
import Foundation
import ScreenOCRRuntime

private struct Arguments {
    let runtime: URL
    let image: URL
    let textFile: URL
    let jsonFile: URL?
    let timeout: Double

    init(_ arguments: [String]) throws {
        let allowed: Set<String> = ["--runtime", "--image", "--text-file", "--json-file", "--timeout"]
        var values: [String: String] = [:]
        var index = 0
        while index < arguments.count {
            let key = arguments[index]
            guard allowed.contains(key), values[key] == nil, index + 1 < arguments.count else {
                throw WorkerError.failure("Usage: ScreenOCRWorker --runtime PATH --image PATH --text-file PATH [--json-file PATH] [--timeout SECONDS]")
            }
            values[key] = arguments[index + 1]
            index += 2
        }
        func path(_ key: String) throws -> URL {
            guard let value = values[key], !value.isEmpty, !value.contains("\0") else {
                throw WorkerError.failure("Missing or invalid \(key)")
            }
            return URL(fileURLWithPath: value).standardizedFileURL
        }
        runtime = try path("--runtime")
        image = try path("--image")
        textFile = try path("--text-file")
        jsonFile = values["--json-file"] == nil ? nil : try path("--json-file")
        guard let duration = Double(values["--timeout"] ?? "90"), duration.isFinite,
              duration > 0, duration <= 86_400 else {
            throw WorkerError.failure("--timeout must be in (0, 86400] seconds")
        }
        timeout = duration
        guard jsonFile != textFile else { throw WorkerError.failure("Output paths must be distinct") }
    }

    func validate() throws {
        for name in ["Frameworks/libmmmojo.dylib", "Resources/libwxocr.dylib", "bin/screen-ocr-engine", "runtime.json"] {
            let url = runtime.appendingPathComponent(name)
            guard try url.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile == true else {
                throw WorkerError.failure("Missing runtime file: \(name)")
            }
        }
        guard FileManager.default.isExecutableFile(atPath: runtime.appendingPathComponent("bin/screen-ocr-engine").path) else {
            throw WorkerError.failure("OCR engine helper is not executable")
        }
        struct Manifest: Decodable { let format_version: Int }
        let manifest = try JSONDecoder().decode(Manifest.self, from: Data(contentsOf: runtime.appendingPathComponent("runtime.json")))
        guard manifest.format_version == 1 else { throw WorkerError.failure("Unsupported runtime manifest version") }
        guard try image.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile == true else {
            throw WorkerError.failure("Image must be a regular file")
        }
    }
}

/// A separate watchdog remains responsive even when a native Start/Stop call
/// blocks. This process owns the group before the library can launch a child.
private final class DeadlineWatchdog {
    private let timer: DispatchSourceTimer
    private var cancellationStarted: UInt64?

    init(deadline: UInt64) {
        timer = DispatchSource.makeTimerSource(queue: DispatchQueue(label: "screen-ocr.deadline"))
        timer.setEventHandler { [weak self] in
            guard let self else { return }
            let now = DispatchTime.now().uptimeNanoseconds
            if OCRWorkerCancellationSignal() != 0, self.cancellationStarted == nil {
                self.cancellationStarted = now
            }
            let cancellationExpired = self.cancellationStarted.map { now - $0 >= 1_000_000_000 } ?? false
            if cancellationExpired || now >= deadline + 1_500_000_000 {
                Self.killOwnedGroup()
            }
        }
        timer.schedule(deadline: .now(), repeating: .milliseconds(25))
        timer.resume()
    }

    func stop() { timer.cancel() }

    static func killOwnedGroup() -> Never {
        if getpgrp() == getpid() { _ = killpg(getpid(), SIGKILL) }
        _exit(124)
    }
}

private struct Diagnostic: Encodable {
    let result: OCRResponse
    let layout: OCRLayoutResult
    let timings: [String: Double]
}

@main
struct WorkerMain {
    static func main() {
        do {
            let arguments = try Arguments(Array(CommandLine.arguments.dropFirst()))
            try arguments.validate()
            // Foundation.Process may already start us as group leader. Never
            // join a caller's group, and never launch native children before
            // this invariant has been established.
            if getpgrp() != getpid(), setsid() < 0 {
                throw WorkerError.failure("Cannot isolate OCR worker process group: \(String(cString: strerror(errno)))")
            }
            guard getpgrp() == getpid(), OCRWorkerInstallSignals() else {
                throw WorkerError.failure("Cannot configure OCR worker lifecycle")
            }
            let started = DispatchTime.now().uptimeNanoseconds
            let deadline = started + UInt64(arguments.timeout * 1_000_000_000)
            let watchdog = DeadlineWatchdog(deadline: deadline)
            defer { watchdog.stop() }
            let session = try MojoSession(runtime: arguments.runtime)
            let response: OCRResponse
            let ready: UInt64
            let recognized: UInt64
            let shutdownStarted: UInt64
            let finished: UInt64
            do {
                try session.open()
                try session.waitUntilReady(deadline: deadline)
                ready = DispatchTime.now().uptimeNanoseconds
                response = try session.recognize(image: arguments.image, deadline: deadline)
                recognized = DispatchTime.now().uptimeNanoseconds
                shutdownStarted = DispatchTime.now().uptimeNanoseconds
                session.close()
                guard OCRWorkerReapProcessGroup() else { DeadlineWatchdog.killOwnedGroup() }
                finished = DispatchTime.now().uptimeNanoseconds
            } catch {
                session.close()
                guard OCRWorkerReapProcessGroup() else { DeadlineWatchdog.killOwnedGroup() }
                throw error
            }
            let cancellation = OCRWorkerCancellationSignal()
            if cancellation != 0 { throw WorkerError.cancelled(cancellation) }
            guard response.errorCode == 0 else {
                throw WorkerError.failure("OCR engine returned error \(response.errorCode)")
            }
            let layout = try OCRLayout.reconstruct(response)
            let milliseconds: (UInt64, UInt64) -> Double = { Double($1 - $0) / 1_000_000 }
            let diagnostic = Diagnostic(result: response, layout: layout, timings: [
                "worker_startup_ms": milliseconds(started, ready),
                "worker_recognition_ms": milliseconds(ready, recognized),
                "worker_shutdown_ms": milliseconds(shutdownStarted, finished),
                "worker_total_ms": milliseconds(started, finished),
            ])
            if let jsonFile = arguments.jsonFile {
                let encoder = JSONEncoder()
                encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
                try writePrivate(encoder.encode(diagnostic), to: jsonFile)
            }
            try writePrivate(Data(layout.text.utf8), to: arguments.textFile)
        } catch {
            fputs("ScreenOCRWorker: \(error.localizedDescription)\n", stderr)
            if case WorkerError.cancelled(let signal) = error { exit(128 + signal) }
            if case WorkerError.timeout = error { exit(124) }
            exit(1)
        }
    }

    private static func writePrivate(_ data: Data, to url: URL) throws {
        let descriptor = Darwin.open(url.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, mode_t(0o600))
        guard descriptor >= 0 else {
            throw WorkerError.failure("Cannot create output \(url.path): \(String(cString: strerror(errno)))")
        }
        var succeeded = false
        defer {
            Darwin.close(descriptor)
            if !succeeded { _ = unlink(url.path) }
        }
        try data.withUnsafeBytes { bytes in
            var written = 0
            while written < bytes.count {
                let count = Darwin.write(descriptor, bytes.baseAddress!.advanced(by: written), bytes.count - written)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { throw WorkerError.failure("Cannot write OCR result: \(String(cString: strerror(errno)))") }
                written += count
            }
        }
        succeeded = true
    }
}
