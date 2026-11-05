import Foundation
import Darwin

/// Owns a single capture/recognition job. All observable state and callbacks
/// run on the main actor; native inference stays in an isolated CLI process.
@MainActor
public final class OCRService {
    public private(set) var state: OCRState = .idle
    public var onStateChange: ((OCRState) -> Void)?
    public var isBusy: Bool { job != nil }

    private enum Cancellation { case user, timeout }
    private final class Job {
        let configuration: OCRConfiguration
        let directory: URL
        let image: URL
        let result: URL
        let completion: (Result<OCRResult, Error>) -> Void
        var process: Process?
        var logHandles: [FileHandle] = []
        var errorLog: URL?
        var cancellation: Cancellation?
        var timeout: DispatchWorkItem?
        var escalation: DispatchWorkItem?

        init(configuration: OCRConfiguration, directory: URL, image: URL?, completion: @escaping (Result<OCRResult, Error>) -> Void) {
            self.configuration = configuration
            self.directory = directory
            self.image = image ?? directory.appendingPathComponent("capture.png")
            self.result = directory.appendingPathComponent("result.txt")
            self.completion = completion
        }

        func closeLogs() {
            for handle in logHandles { try? handle.close() }
            logHandles.removeAll()
        }

        func cleanUp() {
            timeout?.cancel()
            escalation?.cancel()
            closeLogs()
            try? FileManager.default.removeItem(at: directory)
        }
    }

    private let captureExecutable: URL
    private let recognitionTimeout: TimeInterval
    private let temporaryDirectory: URL
    private var job: Job?

    public convenience init(recognitionTimeout: TimeInterval = 95) {
        self.init(captureExecutable: URL(fileURLWithPath: "/usr/sbin/screencapture"),
                  recognitionTimeout: recognitionTimeout,
                  temporaryDirectory: FileManager.default.temporaryDirectory)
    }

    internal init(captureExecutable: URL, recognitionTimeout: TimeInterval,
                  temporaryDirectory: URL) {
        precondition(recognitionTimeout.isFinite && recognitionTimeout > 0)
        self.captureExecutable = captureExecutable
        self.recognitionTimeout = recognitionTimeout
        self.temporaryDirectory = temporaryDirectory
    }

    deinit {
        if let process = job?.process, process.isRunning { process.interrupt() }
    }

    public func capture(configuration: OCRConfiguration, completion: @escaping (Result<OCRResult, Error>) -> Void) {
        begin(configuration: configuration, image: nil, completion: completion)
    }

    /// Useful for image-file workflows and diagnostics; never copies or deletes
    /// the caller's input image. The caller owns any clipboard update.
    public func recognize(imageURL: URL, configuration: OCRConfiguration,
                          completion: @escaping (Result<OCRResult, Error>) -> Void) {
        begin(configuration: configuration, image: imageURL, completion: completion)
    }

    public func cancel() {
        guard let job else { return }
        cancel(job, reason: .user)
    }

    private func setState(_ state: OCRState) {
        self.state = state
        onStateChange?(state)
    }

    private func begin(configuration: OCRConfiguration, image: URL?,
                       completion: @escaping (Result<OCRResult, Error>) -> Void) {
        guard job == nil else { completion(.failure(OCRError.busy)); return }
        do {
            try configuration.validate()
            if let image {
                let values = try image.resourceValues(forKeys: [.isRegularFileKey])
                guard values.isRegularFile == true else { throw OCRError.resultFile }
            }
            let directory = temporaryDirectory.appendingPathComponent("screen-ocr-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                    attributes: [.posixPermissions: 0o700])
            let job = Job(configuration: configuration, directory: directory, image: image, completion: completion)
            self.job = job
            if image == nil {
                try launch(job, executable: captureExecutable, arguments: ["-i", "-x", job.image.path], stage: .capturing)
            } else {
                try recognize(job)
            }
        } catch {
            if let job { finish(job, .failure(error)) }
            else { completion(.failure(error)) }
        }
    }

    private func launch(_ job: Job, executable: URL, arguments: [String], stage: OCRState) throws {
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.currentDirectoryURL = job.directory
        process.standardInput = FileHandle.nullDevice
        // Use files rather than pipes: OCR can return arbitrarily long Unicode
        // text, and a subprocess must never wait for the UI to drain a pipe.
        for name in ["stdout", "stderr"] {
            let path = job.directory.appendingPathComponent("\(stage)-\(name).log")
            guard FileManager.default.createFile(atPath: path.path, contents: nil,
                                                attributes: [.posixPermissions: 0o600]) else {
                throw OCRError.resultFile
            }
            let handle = try FileHandle(forWritingTo: path)
            job.logHandles.append(handle)
            if name == "stdout" { process.standardOutput = handle }
            else { process.standardError = handle; job.errorLog = path }
        }
        process.terminationHandler = { [weak self, job] process in
            DispatchQueue.main.async {
                guard let self else { job.cleanUp(); return }
                self.exited(job, process: process, stage: stage)
            }
        }
        job.process = process
        do { try process.run() }
        catch {
            process.terminationHandler = nil
            job.process = nil
            throw error
        }
        setState(stage)
        if stage == .recognizing {
            let timeout = DispatchWorkItem { [weak self, weak job] in
                guard let self, let job, self.job === job else { return }
                self.cancel(job, reason: .timeout)
            }
            job.timeout = timeout
            DispatchQueue.main.asyncAfter(deadline: .now() + recognitionTimeout, execute: timeout)
        }
    }

    private func recognize(_ job: Job) throws {
        try launch(job, executable: job.configuration.workerURL,
                   arguments: ["--runtime", job.configuration.runtimeURL.path,
                               "--image", job.image.path,
                               "--text-file", job.result.path,
                               "--timeout", String(min(90, recognitionTimeout))], stage: .recognizing)
    }

    private func exited(_ job: Job, process: Process, stage: OCRState) {
        guard self.job === job, job.process === process else { return }
        if stage == .recognizing {
            // The native worker creates its own group before loading the engine.
            // Also remove descendants left by an abnormal worker exit.
            terminateWorkerGroup(process.processIdentifier)
        }
        job.process = nil
        job.closeLogs()
        job.timeout?.cancel()
        job.escalation?.cancel()
        if let cancellation = job.cancellation {
            finish(job, cancellation == .timeout ? .failure(OCRError.timeout) : .success(.cancelled))
            return
        }
        let detail = errorTail(job)
        if stage == .capturing {
            let values = try? job.image.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
            let hasImage = values?.isRegularFile == true && (values?.fileSize ?? 0) > 0
            if !hasImage && detail.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                finish(job, .success(.cancelled))
            } else if process.terminationStatus != 0 || !hasImage {
                finish(job, .failure(OCRError.capture(detail)))
            } else {
                do { try recognize(job) }
                catch { finish(job, .failure(error)) }
            }
        } else if process.terminationReason == .exit && process.terminationStatus == 124 {
            finish(job, .failure(OCRError.timeout))
        } else if process.terminationStatus != 0 {
            let explanation = detail.isEmpty
                ? "OCR 工作进程\(process.terminationReason == .uncaughtSignal ? "被信号终止" : "退出")（\(process.terminationStatus)）。"
                : detail
            finish(job, .failure(OCRError.recognition(explanation)))
        } else {
            do {
                let values = try job.result.resourceValues(forKeys: [.isRegularFileKey])
                guard values.isRegularFile == true else { throw OCRError.resultFile }
                let data = try Data(contentsOf: job.result)
                guard let text = String(data: data, encoding: .utf8) else { throw OCRError.resultFile }
                finish(job, .success(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? .empty : .text(text)))
            } catch { finish(job, .failure(OCRError.resultFile)) }
        }
    }

    private func errorTail(_ job: Job) -> String {
        guard let url = job.errorLog, let handle = try? FileHandle(forReadingFrom: url) else { return "" }
        defer { try? handle.close() }
        guard let length = try? handle.seekToEnd() else { return "" }
        try? handle.seek(toOffset: length > 4096 ? length - 4096 : 0)
        guard let data = try? handle.readToEnd() else { return "" }
        return String(decoding: data, as: UTF8.self)
    }

    private func cancel(_ job: Job, reason: Cancellation) {
        guard self.job === job, job.cancellation == nil else { return }
        job.cancellation = reason
        let wasCapturing = state == .capturing
        setState(.cancelling)
        guard let process = job.process, process.isRunning else {
            // Its termination callback may already be queued. Let that callback
            // perform cleanup before publishing completion.
            return
        }
        if wasCapturing { process.terminate() }
        else { process.interrupt() }
        // Allow native shutdown to finish, then bound even a stuck native call.
        // Kill the whole isolated worker group, never just its parent process.
        let escalation = DispatchWorkItem { [weak self, weak job, weak process] in
            guard let self, let job, let process,
                  self.job === job, job.process === process, process.isRunning else { return }
            if !wasCapturing { self.terminateWorkerGroup(process.processIdentifier) }
            if process.isRunning { Darwin.kill(process.processIdentifier, SIGKILL) }
        }
        job.escalation = escalation
        DispatchQueue.main.asyncAfter(deadline: .now() + 2, execute: escalation)
    }

    private func terminateWorkerGroup(_ pid: pid_t) {
        guard pid > 1, pid != Darwin.getpgrp() else { return }
        let group = Darwin.getpgid(pid)
        // A live worker must own the group; a reaped worker may have left a
        // group bearing its PID. Never signal a live worker's inherited group.
        if group == pid || (group == -1 && errno == ESRCH) {
            Darwin.kill(-pid, SIGKILL)
        }
    }

    private func finish(_ job: Job, _ result: Result<OCRResult, Error>) {
        guard self.job === job else { return }
        job.cleanUp()
        self.job = nil
        setState(.idle)
        job.completion(result)
    }
}
