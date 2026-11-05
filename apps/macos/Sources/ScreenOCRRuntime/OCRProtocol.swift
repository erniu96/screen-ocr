import Foundation

public enum OCRProtocolError: LocalizedError {
    case malformed(String)

    public var errorDescription: String? {
        switch self { case .malformed(let detail): return detail }
    }
}

/// Exact port of the captured native protocol contract in protocol.py.
/// Unknown fields are skipped by protobuf wire type, never assigned meanings.
public enum OCRProtocol {
    private enum Value {
        case integer(UInt64)
        case bytes([UInt8])
    }

    private struct Reader {
        let bytes: [UInt8]
        var position = 0

        mutating func take(_ count: UInt64) throws -> [UInt8] {
            guard count <= UInt64(bytes.count - position) else {
                throw OCRProtocolError.malformed("Truncated protobuf field")
            }
            let end = position + Int(count)
            defer { position = end }
            return Array(bytes[position..<end])
        }

        mutating func varint() throws -> UInt64 {
            var value: UInt64 = 0
            for index in 0..<10 {
                let byte = try take(1)[0]
                guard index != 9 || byte <= 1 else {
                    throw OCRProtocolError.malformed("Protobuf varint exceeds uint64")
                }
                value |= UInt64(byte & 0x7f) << (index * 7)
                if byte < 0x80 { return value }
            }
            throw OCRProtocolError.malformed("Unterminated protobuf varint")
        }

        mutating func tag() throws -> (number: Int, wire: UInt64) {
            let tag = try varint()
            let number = tag >> 3
            let wire = tag & 7
            guard number >= 1 && number <= 0x1fffffff else {
                throw OCRProtocolError.malformed("Invalid protobuf field number")
            }
            guard wire <= 5 else { throw OCRProtocolError.malformed("Invalid protobuf wire type") }
            return (Int(number), wire)
        }

        mutating func skipGroup(_ number: Int, depth: Int = 0) throws {
            guard depth < 64 else { throw OCRProtocolError.malformed("Protobuf groups nested too deeply") }
            while position < bytes.count {
                let child = try tag()
                if child.wire == 4 {
                    guard child.number == number else {
                        throw OCRProtocolError.malformed("Mismatched protobuf end group")
                    }
                    return
                }
                if child.wire == 3 { try skipGroup(child.number, depth: depth + 1) }
                else { _ = try value(child.wire) }
            }
            throw OCRProtocolError.malformed("Unterminated protobuf group")
        }

        mutating func value(_ wire: UInt64) throws -> Value {
            switch wire {
            case 0: return .integer(try varint())
            case 1: return .bytes(try take(8))
            case 2:
                let length = try varint()
                return .bytes(try take(length))
            case 5: return .bytes(try take(4))
            default: throw OCRProtocolError.malformed("Unexpected protobuf group marker")
            }
        }
    }

    private static func fields(_ payload: [UInt8], expected: [Int: UInt64],
                               body: (Int, Value) throws -> Void) throws {
        var reader = Reader(bytes: payload)
        while reader.position < payload.count {
            let tag = try reader.tag()
            if let wire = expected[tag.number], wire != tag.wire {
                throw OCRProtocolError.malformed("Wrong wire type for protobuf field \(tag.number)")
            }
            if tag.wire == 3 { try reader.skipGroup(tag.number); continue }
            guard tag.wire != 4 else { throw OCRProtocolError.malformed("Unexpected protobuf end group") }
            let value = try reader.value(tag.wire)
            if expected[tag.number] != nil { try body(tag.number, value) }
        }
    }

    private static func integer(_ value: Value) throws -> UInt64 {
        guard case .integer(let number) = value else {
            throw OCRProtocolError.malformed("Expected protobuf integer")
        }
        return number
    }

    private static func bytes(_ value: Value) throws -> [UInt8] {
        guard case .bytes(let data) = value else {
            throw OCRProtocolError.malformed("Expected protobuf bytes")
        }
        return data
    }

    private static func text(_ value: Value) throws -> String {
        guard let text = String(bytes: try bytes(value), encoding: .utf8) else {
            throw OCRProtocolError.malformed("Invalid UTF-8 in OCR text")
        }
        return text
    }

    private static func float(_ value: Value) throws -> Double {
        let data = try bytes(value)
        let bits = data.enumerated().reduce(UInt32(0)) { $0 | (UInt32($1.element) << ($1.offset * 8)) }
        return Double(Float(bitPattern: bits))
    }

    private static func varint(_ value: UInt64) -> [UInt8] {
        var value = value
        var encoded: [UInt8] = []
        while value > 0x7f {
            encoded.append(UInt8(value & 0x7f) | 0x80)
            value >>= 7
        }
        encoded.append(UInt8(value))
        return encoded
    }

    public static func encodeRequest(taskID: UInt32, imagePath: String) throws -> Data {
        guard taskID >= 1 && taskID <= 0x7fffffff else {
            throw OCRProtocolError.malformed("task_id must be in [1, 2147483647]")
        }
        guard !imagePath.isEmpty && !imagePath.utf8.contains(0) else {
            throw OCRProtocolError.malformed("image_path must be a nonempty, NUL-free string")
        }
        let path = Array(imagePath.utf8)
        let requestType: [UInt8] = [0x08, 0x01, 0x10, 0x01, 0x18, 0x00]
        return Data([0x08] + varint(UInt64(taskID)) + [0x12] + varint(UInt64(path.count)) + path
                    + [0x32, UInt8(requestType.count)] + requestType)
    }

    public static func decodeSupport(_ data: Data) throws -> Bool {
        var supported = false
        try fields(Array(data), expected: [1: 0]) { _, value in supported = try integer(value) != 0 }
        return supported
    }

    private static func validateBox(_ payload: [UInt8]) throws {
        try fields(payload, expected: [1: 2, 2: 2, 3: 2, 4: 2]) { _, point in
            try fields(bytes(point), expected: [1: 5, 2: 5]) { _, _ in }
        }
    }

    private static func validateCharacter(_ payload: [UInt8]) throws {
        try fields(payload, expected: [1: 2, 2: 2]) { number, value in
            if number == 1 { try validateBox(bytes(value)) }
            else { _ = try text(value) }
        }
    }

    private static func decodeLine(_ payload: [UInt8]) throws -> OCRFragment {
        var line = OCRFragment()
        try fields(payload, expected: [1: 2, 2: 2, 3: 5, 4: 2, 5: 5, 6: 5, 7: 5, 8: 5, 9: 0, 10: 2]) {
            number, value in
            switch number {
            case 1, 10: try validateBox(bytes(value))
            case 2: line.text = try text(value)
            case 3: line.rate = try float(value)
            case 4: try validateCharacter(bytes(value))
            case 5: line.left = try float(value)
            case 6: line.top = try float(value)
            case 7: line.right = try float(value)
            case 8: line.bottom = try float(value)
            default: break
            }
        }
        return line
    }

    private static func dimension(_ value: Value) throws -> UInt32 {
        guard let result = UInt32(exactly: try integer(value)) else {
            throw OCRProtocolError.malformed("Image dimension exceeds uint32")
        }
        return result
    }

    private static func mergeResult(_ payload: [UInt8], into result: inout OCRResponse) throws {
        try fields(payload, expected: [3: 2, 4: 0, 5: 0, 6: 2, 7: 0]) { number, value in
            switch number {
            case 3: result.fragments.append(try decodeLine(bytes(value)))
            case 4: result.width = try dimension(value)
            case 5: result.height = try dimension(value)
            case 6: _ = try text(value)
            default: break
            }
        }
    }

    public static func decodeResponse(_ data: Data) throws -> (taskID: UInt64, response: OCRResponse) {
        var taskID: UInt64 = 0
        var hasError = false
        var hasResult = false
        var result = OCRResponse()
        try fields(Array(data), expected: [1: 0, 2: 0, 3: 2, 4: 2, 5: 2, 6: 2]) { number, value in
            switch number {
            case 1: taskID = try integer(value)
            case 2:
                hasError = true
                result.errorCode = Int(Int32(bitPattern: UInt32(truncatingIfNeeded: try integer(value))))
            case 3:
                hasResult = true
                try mergeResult(bytes(value), into: &result)
            case 4: try fields(bytes(value), expected: [1: 0, 2: 0, 3: 0]) { _, _ in }
            default: break
            }
        }
        if !hasResult && !hasError { result.errorCode = -1 }
        return (taskID, result)
    }
}
