import Foundation

public struct OCRConfiguration: Equatable {
    public let runtimeURL: URL
    public let workerURL: URL

    public init(runtimeURL: URL, workerURL: URL) {
        self.runtimeURL = runtimeURL.standardizedFileURL
        self.workerURL = workerURL.standardizedFileURL
    }

    public static func bundled(in bundle: Bundle = .main) throws -> OCRConfiguration {
        guard let resources = bundle.resourceURL, let executable = bundle.executableURL else {
            throw OCRError.configuration("无法定位 App 内的 OCR 运行文件，请重新构建完整 App。")
        }
        let configuration = OCRConfiguration(
            runtimeURL: resources.appendingPathComponent("Runtime", isDirectory: true),
            workerURL: executable.deletingLastPathComponent().appendingPathComponent("ScreenOCRWorker")
        )
        try configuration.validate()
        return configuration
    }

    public func validate() throws {
        var directory: ObjCBool = false
        guard runtimeURL.isFileURL, workerURL.isFileURL,
              FileManager.default.fileExists(atPath: runtimeURL.path, isDirectory: &directory),
              directory.boolValue else {
            throw OCRError.configuration("App 缺少 OCR 运行文件，请重新构建完整 App。")
        }
        guard FileManager.default.isExecutableFile(atPath: workerURL.path) else {
            throw OCRError.configuration("App 缺少原生 OCR 工作程序，请重新构建完整 App。")
        }
        guard FileManager.default.isExecutableFile(atPath: runtimeURL.appendingPathComponent("bin/screen-ocr-engine").path) else {
            throw OCRError.configuration("App 缺少 OCR 引擎入口，请重新构建完整 App。")
        }
        for path in ["Frameworks/libmmmojo.dylib", "Resources/libwxocr.dylib", "runtime.json"] {
            let file = runtimeURL.appendingPathComponent(path)
            let values = try? file.resourceValues(forKeys: [.isRegularFileKey])
            guard values?.isRegularFile == true else {
                throw OCRError.configuration("App 缺少 OCR 运行文件：\(path)。请重新构建完整 App。")
            }
        }
    }
}

public enum OCRState: Equatable {
    case idle, capturing, recognizing, cancelling
}

public enum OCRResult: Equatable {
    case text(String)
    case empty
    case cancelled
}

public enum OCRError: LocalizedError {
    case configuration(String)
    case busy
    case capture(String)
    case recognition(String)
    case timeout
    case resultFile

    public var errorDescription: String? {
        switch self {
        case .configuration(let message): return message
        case .busy: return "正在处理上一张截图。"
        case .capture(let detail): return "截图失败。请检查屏幕录制权限。\n\(detail)"
        case .recognition(let detail): return "文字识别失败。\n\(detail)"
        case .timeout: return "文字识别超时，请缩小截图区域后重试。"
        case .resultFile: return "未能读取有效的 OCR 结果。"
        }
    }
}
