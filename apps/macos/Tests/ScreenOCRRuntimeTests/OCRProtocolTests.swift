import Foundation
import XCTest
@testable import ScreenOCRRuntime

final class OCRProtocolTests: XCTestCase {
    private func hex(_ text: String) -> Data {
        let characters = Array(text)
        return Data(stride(from: 0, to: characters.count, by: 2).map {
            UInt8(String(characters[$0...($0 + 1)]), radix: 16)!
        })
    }

    private func field(_ number: UInt8, _ bytes: Data) -> Data {
        precondition(number < 16 && bytes.count < 128)
        return Data([number * 8 + 2, UInt8(bytes.count)]) + bytes
    }

    private func fixture(_ name: String) -> URL {
        var directory = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { directory.deleteLastPathComponent() }
        return directory.appendingPathComponent("tests/fixtures/\(name)")
    }

    func testExactWireRequestAndUTF8ByteLengths() throws {
        XCTAssertEqual(try OCRProtocol.encodeRequest(taskID: 2, imagePath: "/tmp/a.png"),
                       hex("0802120a2f746d702f612e706e673206080110011800"))
        let path = "/tmp/中文.png"
        let encoded = try OCRProtocol.encodeRequest(taskID: 128, imagePath: path)
        XCTAssertEqual(encoded.prefix(4), hex("08800112"))
        XCTAssertEqual(Int(encoded[4]), path.utf8.count)
        XCTAssertNotNil(encoded.range(of: Data(path.utf8)))
        XCTAssertEqual(try OCRProtocol.encodeRequest(taskID: 0x7fffffff, imagePath: "/" + String(repeating: "x", count: 127)).prefix(9),
                       hex("08ffffffff07128001"))
        for task: UInt32 in [0, 0x80000000, UInt32.max] {
            XCTAssertThrowsError(try OCRProtocol.encodeRequest(taskID: task, imagePath: "/a"))
        }
        for path in ["", "a\0b"] { XCTAssertThrowsError(try OCRProtocol.encodeRequest(taskID: 2, imagePath: path)) }
    }

    func testSupportDefaultsDuplicatesAndAllUnknownWireTypes() throws {
        XCTAssertFalse(try OCRProtocol.decodeSupport(Data()))
        XCTAssertFalse(try OCRProtocol.decodeSupport(hex("0800")))
        XCTAssertTrue(try OCRProtocol.decodeSupport(hex("0802")))
        XCTAssertFalse(try OCRProtocol.decodeSupport(hex("08010800")))
        let unknown = hex("109601") + Data([0x19]) + Data("12345678".utf8)
            + hex("22026162") + Data([0x2d]) + Data("1234".utf8) + hex("33080134")
        XCTAssertTrue(try OCRProtocol.decodeSupport(unknown + hex("0801")))
    }

    func testHandEncodedResponseAndSignedErrors() throws {
        var line = field(2, Data("中文 OCR".utf8))
        for (number, value): (UInt8, Float) in [(3, 0.875), (5, 10.5), (6, 20.25), (7, 80), (8, 40.5)] {
            let bits = value.bitPattern
            line.append(number * 8 + 5)
            for shift in stride(from: 0, through: 24, by: 8) { line.append(UInt8(truncatingIfNeeded: bits >> shift)) }
        }
        let info = field(3, line) + field(3, field(2, Data("second".utf8))) + hex("20c00228f001")
        let decoded = try OCRProtocol.decodeResponse(hex("08021000") + field(3, info))
        XCTAssertEqual(decoded.taskID, 2)
        XCTAssertEqual(decoded.response, OCRResponse(width: 320, height: 240, fragments: [
            OCRFragment(text: "中文 OCR", left: 10.5, top: 20.25, right: 80, bottom: 40.5, rate: 0.875),
            OCRFragment(text: "second"),
        ]))
        XCTAssertEqual(try OCRProtocol.decodeResponse(hex("080210ffffffffffffffffff01")).response.errorCode, -1)
        XCTAssertEqual(try OCRProtocol.decodeResponse(hex("1080808080f8ffffffff01")).response.errorCode, -2147483648)
    }

    func testResponseDefaultsAndSingularMessageMerge() throws {
        XCTAssertEqual(try OCRProtocol.decodeResponse(Data()).response.errorCode, -1)
        XCTAssertEqual(try OCRProtocol.decodeResponse(hex("1000")).response.errorCode, 0)
        XCTAssertEqual(try OCRProtocol.decodeResponse(hex("1a00")).response.errorCode, 0)
        let result = try OCRProtocol.decodeResponse(field(3, hex("2064")) + field(3, hex("2832"))).response
        XCTAssertEqual(result.width, 100)
        XCTAssertEqual(result.height, 50)
        let message = hex("0805") + field(3, field(3, field(2, Data("hello".utf8)) + field(15, Data("unused".utf8))))
            + field(15, Data("ignored".utf8))
        XCTAssertEqual(try OCRProtocol.decodeResponse(message).response.fragments.map(\.text), ["hello"])
    }

    func testMalformedEncodingIsRejected() {
        let payloads: [Data] = [
            hex("08"), hex("80"), hex("00"), hex("0e"),
            hex("08") + Data(repeating: 0x80, count: 10),
            hex("08") + Data(repeating: 0xff, count: 9) + hex("02"),
            hex("11") + Data("abc".utf8), hex("15") + Data("abc".utf8),
            hex("1204") + Data("abc".utf8), hex("130801"), hex("131c"), hex("14"),
            Data(repeating: 0x13, count: 65) + Data(repeating: 0x14, count: 65),
            hex("12ffffffffffffffffff01"), // Declared length cannot overflow Int.
        ]
        for payload in payloads { XCTAssertThrowsError(try OCRProtocol.decodeSupport(payload), "\(payload as NSData)") }
        XCTAssertThrowsError(try OCRProtocol.decodeSupport(hex("0a00")))
        XCTAssertThrowsError(try OCRProtocol.decodeResponse(hex("0a00")))
        XCTAssertThrowsError(try OCRProtocol.decodeResponse(field(3, hex("2500000000"))))
        XCTAssertThrowsError(try OCRProtocol.decodeResponse(field(3, field(3, hex("1801")))))
        XCTAssertThrowsError(try OCRProtocol.decodeResponse(field(3, field(3, hex("1d000000")))))
        XCTAssertThrowsError(try OCRProtocol.decodeResponse(field(3, hex("208080808010"))))
    }

    func testDeclaredNestedFieldsAreValidatedEvenWhenNotReturned() {
        let invalidLines = [
            field(1, field(1, hex("0d00"))),
            field(4, field(2, hex("ff"))),
            field(10, hex("0800")),
            field(2, hex("ff")),
        ]
        for line in invalidLines { XCTAssertThrowsError(try OCRProtocol.decodeResponse(field(3, field(3, line)))) }
        XCTAssertThrowsError(try OCRProtocol.decodeResponse(field(4, hex("0a00"))))
        XCTAssertThrowsError(try OCRProtocol.decodeResponse(field(3, field(6, hex("ff")))))
    }

    func testCapturedMacOSAndLinuxMessagesAgainstIndependentDecode() throws {
        struct Record: Decodable {
            let request_id: Int?
            let hex: String?
            let size: Int?
        }
        struct Envelope<T: Decodable>: Decodable { let protobuf: T }
        struct Request: Decodable { let task_id: String; let pic_path: String }
        struct Support: Decodable { let supported: Bool }
        struct Response: Decodable {
            struct Result: Decodable { let lines: [OCRFragment]; let img_width: UInt32; let img_height: UInt32 }
            let task_id: String
            let res: Result
        }
        for name in ["macos-capture.jsonl", "linux-capture.jsonl"] {
            let decoder = JSONDecoder()
            let lines = try String(contentsOf: fixture(name), encoding: .utf8).split(separator: "\n")
            var identifiers: Set<Int> = []
            for line in lines {
                let data = Data(line.utf8)
                let record = try decoder.decode(Record.self, from: data)
                guard let requestID = record.request_id else { continue } // Captured lifecycle events have no payload.
                identifiers.insert(requestID)
                let payload = hex(try XCTUnwrap(record.hex))
                XCTAssertEqual(payload.count, record.size)
                switch requestID {
                case 10001:
                    let expected = try decoder.decode(Envelope<Support>.self, from: data).protobuf
                    XCTAssertEqual(try OCRProtocol.decodeSupport(payload), expected.supported)
                case 10010:
                    let expected = try decoder.decode(Envelope<Request>.self, from: data).protobuf
                    XCTAssertEqual(try OCRProtocol.encodeRequest(taskID: XCTUnwrap(UInt32(expected.task_id)), imagePath: expected.pic_path), payload)
                case 10011:
                    let expected = try decoder.decode(Envelope<Response>.self, from: data).protobuf
                    let actual = try OCRProtocol.decodeResponse(payload)
                    XCTAssertEqual(actual.taskID, UInt64(expected.task_id))
                    XCTAssertEqual(actual.response.errorCode, 0)
                    XCTAssertEqual(actual.response.width, expected.res.img_width)
                    XCTAssertEqual(actual.response.height, expected.res.img_height)
                    XCTAssertEqual(actual.response.fragments.count, expected.res.lines.count)
                    for (observed, reference) in zip(actual.response.fragments, expected.res.lines) {
                        XCTAssertEqual(observed.text, reference.text)
                        for key in [\OCRFragment.left, \.top, \.right, \.bottom, \.rate] {
                            XCTAssertEqual(observed[keyPath: key], reference[keyPath: key], accuracy: 0.0001)
                        }
                    }
                default: XCTFail("Unexpected capture request ID \(requestID)")
                }
            }
            XCTAssertEqual(identifiers, [10001, 10010, 10011])
        }
    }

    func testJSONUsesVerifiedFieldNamesAndRejectsAliases() throws {
        let value = OCRResponse(imagePath: "/test.png", fragments: [OCRFragment(text: "x", right: 10, bottom: 20)])
        let encoded = try JSONEncoder().encode(value)
        let dictionary = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        XCTAssertEqual(Set(dictionary.keys), ["imgpath", "errcode", "width", "height", "ocr_response"])
        XCTAssertEqual(try JSONDecoder().decode(OCRResponse.self, from: encoded), value)
        XCTAssertThrowsError(try JSONDecoder().decode(OCRFragment.self, from: Data(#"{"text":"x","x":0,"y":10,"width":10,"height":20,"rate":1}"#.utf8)))
    }
}
