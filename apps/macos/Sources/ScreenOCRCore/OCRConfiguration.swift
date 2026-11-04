import Foundation

public struct OCRConfiguration: Equatable {
    public let projectDirectory: URL

    public init(projectDirectory: URL) {
        self.projectDirectory = projectDirectory.standardizedFileURL
    }

    public var pythonURL: URL { projectDirectory.appendingPathComponent(".venv/bin/python") }
    public var runtimeURL: URL { projectDirectory.appendingPathComponent(".runtime", isDirectory: true) }

    public func validate() throws {
        var directory: ObjCBool = false
        guard projectDirectory.isFileURL,
              FileManager.default.fileExists(atPath: projectDirectory.path, isDirectory: &directory),
              directory.boolValue else {
            throw OCRError.configuration("请选择 screen-ocr 项目文件夹。")
        }
        guard FileManager.default.isExecutableFile(atPath: pythonURL.path) else {
            throw OCRError.configuration("项目缺少 Python 环境，请先在项目目录运行 uv sync --locked。")
        }
        guard FileManager.default.isExecutableFile(atPath: runtimeURL.appendingPathComponent("bin/screen-ocr-engine").path),
              FileManager.default.fileExists(atPath: runtimeURL.appendingPathComponent("runtime.json").path) else {
            throw OCRError.configuration("项目缺少 OCR 运行时，请先完成 README 中的运行时准备步骤。")
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
