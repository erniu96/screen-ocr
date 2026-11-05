import CMojoBridge
import Foundation
import ScreenOCRRuntime

enum WorkerError: LocalizedError {
    case failure(String)
    case cancelled(Int32)
    case timeout

    var errorDescription: String? {
        switch self {
        case .failure(let message): return message
        case .cancelled: return "OCR cancelled"
        case .timeout: return "OCR exceeded its deadline"
        }
    }
}

/// A one-shot session. The C bridge releases each read buffer before delivery;
/// this condition only protects Swift state shared with native callback threads.
final class MojoSession {
    private let condition = NSCondition()
    private var ready = false
    private var result: OCRResponse?
    private var failure: Error?
    private var closing = false
    private var handle: OpaquePointer?
    private let runtime: URL

    init(runtime: URL) throws {
        self.runtime = runtime
        // Native callbacks can unwind after Stop/Remove return. Keep their
        // context alive until this one-shot worker exits, just like the dylib.
        let context = Unmanaged.passRetained(self).toOpaque()
        var error = [CChar](repeating: 0, count: 4096)
        handle = OCRMojoCreate(runtime.appendingPathComponent("Frameworks/libmmmojo.dylib").path,
            { requestID, bytes, count, context in
                guard let context else { return }
                let owner = Unmanaged<MojoSession>.fromOpaque(context).takeUnretainedValue()
                let data = count == 0 ? Data() : Data(bytes: bytes!, count: count)
                owner.received(requestID: requestID, data: data)
            },
            { bytes, count, context in
                guard let context else { return }
                let owner = Unmanaged<MojoSession>.fromOpaque(context).takeUnretainedValue()
                let data = count == 0 ? Data() : Data(bytes: bytes!, count: count)
                owner.fail(WorkerError.failure(String(decoding: data, as: UTF8.self)))
            }, context, &error, error.count)
        if handle == nil {
            Unmanaged<MojoSession>.fromOpaque(context).release()
            throw WorkerError.failure(String(cString: error))
        }
    }

    func open() throws {
        var error = [CChar](repeating: 0, count: 4096)
        guard OCRMojoOpen(handle, runtime.appendingPathComponent("bin/screen-ocr-engine").path,
                          runtime.appendingPathComponent("Resources/libwxocr.dylib").path,
                          runtime.appendingPathComponent("Resources").path, &error, error.count) else {
            throw WorkerError.failure(String(cString: error))
        }
    }

    func waitUntilReady(deadline: UInt64) throws {
        try wait(deadline: deadline) { ready }
    }

    func recognize(image: URL, deadline: UInt64) throws -> OCRResponse {
        let data = try OCRProtocol.encodeRequest(taskID: 2, imagePath: image.path)
        var error = [CChar](repeating: 0, count: 4096)
        let sent = data.withUnsafeBytes { bytes in
            OCRMojoSend(handle, bytes.bindMemory(to: UInt8.self).baseAddress, bytes.count, &error, error.count)
        }
        guard sent else { throw WorkerError.failure(String(cString: error)) }
        try wait(deadline: deadline) { result != nil }
        condition.lock()
        var response = result!
        condition.unlock()
        response.imagePath = image.path
        return response
    }

    func close() {
        condition.lock()
        closing = true
        condition.unlock()
        if let handle { OCRMojoClose(handle) }
    }

    private func wait(deadline: UInt64, completed: () -> Bool) throws {
        condition.lock()
        defer { condition.unlock() }
        while true {
            let signal = OCRWorkerCancellationSignal()
            if signal != 0 { throw WorkerError.cancelled(signal) }
            if DispatchTime.now().uptimeNanoseconds >= deadline { throw WorkerError.timeout }
            if let failure { throw failure }
            if completed() { return }
            // Poll only to observe async-signal-safe cancellation and the
            // monotonic deadline; native callbacks wake the condition directly.
            _ = condition.wait(until: Date(timeIntervalSinceNow: 0.05))
        }
    }

    private func fail(_ error: Error) {
        condition.lock()
        defer { condition.unlock() }
        guard !closing, failure == nil else { return }
        failure = error
        condition.broadcast()
    }

    private func received(requestID: UInt32, data: Data) {
        do {
            switch requestID {
            case 10001:
                guard try OCRProtocol.decodeSupport(data) else {
                    throw WorkerError.failure("Native engine reports OCR unsupported")
                }
                condition.lock()
                if !closing { ready = true; condition.broadcast() }
                condition.unlock()
            case 10011:
                let decoded = try OCRProtocol.decodeResponse(data)
                guard decoded.taskID == 2 else {
                    throw WorkerError.failure("Unexpected OCR task ID \(decoded.taskID)")
                }
                condition.lock()
                if !closing { result = decoded.response; condition.broadcast() }
                condition.unlock()
            default: break
            }
        } catch {
            fail(WorkerError.failure("Invalid OCR response: \(error.localizedDescription)"))
        }
    }
}
