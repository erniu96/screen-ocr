import Foundation
import XCTest
@testable import ScreenOCRRuntime

final class OCRLayoutTests: XCTestCase {
    private func box(_ text: String, _ left: Double, _ top: Double, _ right: Double, _ bottom: Double) -> OCRFragment {
        OCRFragment(text: text, left: left, top: top, right: right, bottom: bottom, rate: 0.9)
    }

    private func layout(_ fragments: OCRFragment...) throws -> OCRLayoutResult {
        try OCRLayout.reconstruct(OCRResponse(width: 600, height: 400, fragments: fragments))
    }

    func testTwelveShuffledFragmentsRestoreRowsWithoutChangingLetters() throws {
        struct Fixture: Decodable {
            let result: OCRResponse
            let expected_lines: [String]
            let expected_source_indices: [[Int]]
        }
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { url.deleteLastPathComponent() }
        let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: url.appendingPathComponent("tests/fixtures/synthetic-layout-fragments.json")))
        let actual = try OCRLayout.reconstruct(fixture.result)
        XCTAssertEqual(actual.lines.map(\.text), fixture.expected_lines)
        XCTAssertEqual(actual.lines.map(\.sourceIndices), fixture.expected_source_indices)
        XCTAssertEqual(actual.sourceFragmentCount, 12)
        XCTAssertEqual(actual.outputLineCount, 7)
        XCTAssertEqual(actual.lines.flatMap(\.sourceIndices).sorted(), Array(0..<12))
        XCTAssertTrue(actual.text.contains("impove"))
        XCTAssertFalse(actual.text.contains("improve"))
        XCTAssertEqual(actual.algorithm, "geometry_rows_v1")
    }

    func testGeometryRestoresSplitWordAndNextRow() throws {
        let actual = try layout(box("ions", 50, 10, 90, 30), box("next row", 10, 50, 90, 70), box("quest", 0, 10, 49, 30))
        XCTAssertEqual(actual.text, "questions\nnext row")
        XCTAssertEqual(actual.lines[0].sourceIndices, [2, 0])
        XCTAssertEqual(actual.lines[0].separators, [""])
    }

    func testWordSpacingPreservesExistingWhitespace() throws {
        XCTAssertEqual(try layout(box("world", 60, 10, 110, 30), box("hello", 0, 10, 50, 30)).text, "hello world")
        let actual = try layout(box("hello ", 0, 10, 50, 30), box("world", 60, 10, 110, 30))
        XCTAssertEqual(actual.text, "hello world")
        XCTAssertEqual(actual.lines[0].separators, [""])
        XCTAssertEqual(try layout(box("hello\u{001c}", 0, 10, 50, 30), box("world", 60, 10, 110, 30)).text, "hello\u{001c}world")
    }

    func testUnicodePitchMatchesPythonScalarProperties() throws {
        XCTAssertEqual(try layout(box("。", 42.5, 10, 60, 30), box("中文", 0, 10, 40, 30), box("Raw_Case()", 0, 50, 100, 70)).text,
                       "中文。\nRaw_Case()")
        for wide in ["Ａ", "😀", "中"] {
            XCTAssertEqual(try layout(box(wide, 0, 10, 20, 30), box("x", 26, 10, 46, 30)).text, wide + " x")
        }
        let decomposed = "e\u{0301}"
        let actual = try layout(box(decomposed, 0, 10, 10, 30), box("x", 14, 10, 34, 30)).text
        XCTAssertEqual(Array(actual.utf8), Array((decomposed + "x").utf8))
    }

    func testColumnsOverlapsAndTallBoxesStaySeparate() throws {
        let columns = try layout(box("left 2", 0, 50, 50, 70), box("right 1", 250, 10, 310, 30),
                                 box("left 1", 0, 10, 50, 30), box("right 2", 250, 50, 310, 70))
        XCTAssertEqual(columns.text, "left 1\nright 1\nleft 2\nright 2")
        XCTAssertEqual(columns.warnings, ["wide_gaps_kept_separate"])
        let overlaps = try layout(box("abc", 0, 10, 30, 30), box("abd", 1, 10, 31, 30))
        XCTAssertEqual(overlaps.text, "abc\nabd")
        XCTAssertEqual(overlaps.warnings, ["overlapping_boxes_kept_separate"])
        XCTAssertEqual(try layout(box("top", 0, 0, 30, 20), box("tall", 40, 0, 80, 60), box("bottom", 0, 40, 60, 60)).text,
                       "top\ntall\nbottom")
    }

    func testEmptyAndInvalidGeometry() throws {
        let empty = try layout()
        XCTAssertEqual(empty.text, "")
        XCTAssertEqual(empty.lines, [])
        XCTAssertEqual(empty.sourceFragmentCount, 0)
        for invalid in [box("x", 20, 10, 10, 30), box("x", 0, 10, 10, 10),
                        box("x", .nan, 10, 10, 30), box("x", 0, 10, .infinity, 30)] {
            XCTAssertThrowsError(try layout(invalid))
        }
    }

    func testLayoutJSONContainsExactAuditContract() throws {
        let actual = try layout(box("ions", 50, 10, 90, 30), box("quest", 0, 10, 49, 30))
        let encoded = try JSONEncoder().encode(actual)
        let dictionary = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        XCTAssertEqual(Set(dictionary.keys), ["algorithm", "text", "source_fragment_count", "output_line_count", "lines", "warnings"])
        let line = try XCTUnwrap((dictionary["lines"] as? [[String: Any]])?.first)
        XCTAssertEqual(Set(line.keys), ["text", "source_indices", "separators", "left", "top", "right", "bottom"])
        XCTAssertEqual(try JSONDecoder().decode(OCRLayoutResult.self, from: encoded), actual)
    }
}
