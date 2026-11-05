import Foundation

/// The verified rectangle fields returned by the native OCR protocol.
public struct OCRFragment: Codable, Equatable {
    public var text: String
    public var left: Double
    public var top: Double
    public var right: Double
    public var bottom: Double
    public var rate: Double

    public init(text: String = "", left: Double = 0, top: Double = 0,
                right: Double = 0, bottom: Double = 0, rate: Double = 0) {
        self.text = text
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom
        self.rate = rate
    }
}

public struct OCRResponse: Codable, Equatable {
    public var imagePath: String?
    public var errorCode: Int
    public var width: UInt32
    public var height: UInt32
    public var fragments: [OCRFragment]

    public init(imagePath: String? = nil, errorCode: Int = 0, width: UInt32 = 0,
                height: UInt32 = 0, fragments: [OCRFragment] = []) {
        self.imagePath = imagePath
        self.errorCode = errorCode
        self.width = width
        self.height = height
        self.fragments = fragments
    }

    enum CodingKeys: String, CodingKey {
        case imagePath = "imgpath", errorCode = "errcode", width, height
        case fragments = "ocr_response"
    }
}

public struct OCRLayoutLine: Codable, Equatable {
    public let text: String
    public let sourceIndices: [Int]
    public let separators: [String]
    public let left: Double
    public let top: Double
    public let right: Double
    public let bottom: Double

    enum CodingKeys: String, CodingKey {
        case text, separators, left, top, right, bottom
        case sourceIndices = "source_indices"
    }
}

public struct OCRLayoutResult: Codable, Equatable {
    public let algorithm: String
    public let text: String
    public let sourceFragmentCount: Int
    public let outputLineCount: Int
    public let lines: [OCRLayoutLine]
    public let warnings: [String]

    enum CodingKeys: String, CodingKey {
        case algorithm, text, lines, warnings
        case sourceFragmentCount = "source_fragment_count"
        case outputLineCount = "output_line_count"
    }
}
