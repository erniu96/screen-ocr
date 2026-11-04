import Foundation
import XCTest
@testable import ScreenOCRCore

final class OCRServiceTests: XCTestCase {
    private struct Fixture {
        let root: URL
        let configuration: OCRConfiguration
        let capture: URL
        let temporary: URL
        let image: URL
    }

    private func executable(_ text: String, at path: URL) throws {
        try text.write(to: path, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: path.path)
    }

    private func fixture(backend: String, capture: String = "#!/bin/sh\nprintf 'image' > \"$3\"\n") throws -> Fixture {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("Screen OCR 中文 tests \(UUID().uuidString)", isDirectory: true)
        for folder in [".venv/bin", ".runtime/bin", "temporary"] {
            try FileManager.default.createDirectory(at: root.appendingPathComponent(folder), withIntermediateDirectories: true)
        }
        let configuration = OCRConfiguration(projectDirectory: root)
        try executable(backend, at: configuration.pythonURL)
        try executable("#!/bin/sh\nexit 0\n", at: configuration.runtimeURL.appendingPathComponent("bin/screen-ocr-engine"))
        try Data("{}".utf8).write(to: configuration.runtimeURL.appendingPathComponent("runtime.json"))
        let captureURL = root.appendingPathComponent("fake capture")
        try executable(capture, at: captureURL)
        let image = root.appendingPathComponent("original 中文 image.png")
        try Data("fixture image".utf8).write(to: image)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return Fixture(root: root, configuration: configuration, capture: captureURL,
                       temporary: root.appendingPathComponent("temporary"), image: image)
    }

    @MainActor
    private func service(_ fixture: Fixture, timeout: TimeInterval = 3) -> OCRService {
        OCRService(captureExecutable: fixture.capture, recognitionTimeout: timeout,
                   temporaryDirectory: fixture.temporary)
    }

    @MainActor
    private func recognize(_ service: OCRService, fixture: Fixture) async -> Result<OCRResult, Error> {
        await withCheckedContinuation { continuation in
            service.recognize(imageURL: fixture.image, configuration: fixture.configuration) {
                continuation.resume(returning: $0)
            }
        }
    }

    @MainActor
    private func waitForFile(_ file: URL) async throws {
        for _ in 0..<300 {
            if FileManager.default.fileExists(atPath: file.path) { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("Child did not create \(file.lastPathComponent)")
    }

    private func assertClean(_ fixture: Fixture, file: StaticString = #filePath, line: UInt = #line) throws {
        XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: fixture.temporary.path).isEmpty, file: file, line: line)
        XCTAssertEqual(try String(contentsOf: fixture.image, encoding: .utf8), "fixture image", file: file, line: line)
    }

    @MainActor
    func testLargeUnicodeOutputAndDiagnosticPipesDoNotBlock() async throws {
        let fixture = try fixture(backend: """
        #!/usr/bin/perl
        use utf8;
        die "contract" unless @ARGV == 8 && $ARGV[0] eq '-m' && $ARGV[1] eq 'screen_ocr.cli' && $ARGV[2] eq 'recognize' && $ARGV[4] eq '--runtime' && $ARGV[6] eq '--text-file';
        print STDOUT ('x' x 262144);
        print STDERR ('y' x 262144);
        open(my $out, '>', $ARGV[7]) or die $!;
        binmode $out, ':encoding(UTF-8)';
        print {$out} ("中文🙂\n" x 40000);
        close($out);
        """)
        let service = service(fixture)
        var states: [OCRState] = []
        service.onStateChange = { states.append($0) }
        let outcome = try await recognize(service, fixture: fixture).get()
        XCTAssertEqual(outcome, .text(String(repeating: "中文🙂\n", count: 40000)))
        XCTAssertEqual(states, [.recognizing, .idle])
        XCTAssertFalse(service.isBusy)
        try assertClean(fixture)
    }

    @MainActor
    func testCaptureEscapeDoesNotLaunchOCR() async throws {
        let fixture = try fixture(backend: "#!/bin/sh\nexit 99\n", capture: "#!/bin/sh\nexit 1\n")
        let service = service(fixture)
        let outcome: Result<OCRResult, Error> = await withCheckedContinuation { continuation in
            service.capture(configuration: fixture.configuration) { continuation.resume(returning: $0) }
        }
        XCTAssertEqual(try outcome.get(), .cancelled)
        try assertClean(fixture)
    }

    @MainActor
    func testSuccessfulCaptureRunsRecognitionAndCleansImage() async throws {
        let fixture = try fixture(backend: "#!/bin/sh\nprintf 'capture result' > \"$8\"\n")
        let service = service(fixture)
        var states: [OCRState] = []
        service.onStateChange = { states.append($0) }
        let outcome: Result<OCRResult, Error> = await withCheckedContinuation { continuation in
            service.capture(configuration: fixture.configuration) { continuation.resume(returning: $0) }
        }
        XCTAssertEqual(try outcome.get(), .text("capture result"))
        XCTAssertEqual(states, [.capturing, .recognizing, .idle])
        try assertClean(fixture)
    }

    @MainActor
    func testEmptyTextIsAnExplicitOutcome() async throws {
        let fixture = try fixture(backend: "#!/bin/sh\nprintf ' \n\t' > \"$8\"\n")
        let service = service(fixture)
        let outcome = try await recognize(service, fixture: fixture).get()
        XCTAssertEqual(outcome, .empty)
        try assertClean(fixture)
    }

    @MainActor
    func testRecognitionFailureBoundsDiagnosticTailAndCleansFiles() async throws {
        let fixture = try fixture(backend: "#!/usr/bin/perl\nprint STDERR ('x' x 262144); print STDERR 'useful end'; exit 7;\n")
        let service = service(fixture)
        switch await recognize(service, fixture: fixture) {
        case .success: XCTFail("Expected failure")
        case .failure(let error):
            XCTAssertTrue(error.localizedDescription.hasSuffix("useful end"))
            XCTAssertLessThan(error.localizedDescription.utf8.count, 4200)
        }
        try assertClean(fixture)
    }

    private let cancellableBackend = """
    #!/usr/bin/perl
    $SIG{INT} = sub { open(my $done, '>', 'cleanup-done'); print {$done} 'clean'; close($done); exit 130; };
    open(my $ready, '>', 'ready'); print {$ready} $$; close($ready);
    while (1) { select(undef, undef, undef, 0.02); }
    """

    @MainActor
    func testCancelWaitsForCooperativeCleanupAndRejectsDuplicateJob() async throws {
        let fixture = try fixture(backend: cancellableBackend)
        let service = service(fixture)
        let completion = expectation(description: "cancelled after cleanup")
        service.recognize(imageURL: fixture.image, configuration: fixture.configuration) { outcome in
            XCTAssertEqual(try? outcome.get(), .cancelled)
            XCTAssertTrue(FileManager.default.fileExists(atPath: fixture.root.appendingPathComponent("cleanup-done").path))
            completion.fulfill()
        }
        try await waitForFile(fixture.root.appendingPathComponent("ready"))
        var duplicateRejected = false
        service.capture(configuration: fixture.configuration) { outcome in
            if case .failure(OCRError.busy) = outcome { duplicateRejected = true }
        }
        XCTAssertTrue(duplicateRejected)
        service.cancel()
        XCTAssertEqual(service.state, .cancelling)
        await fulfillment(of: [completion], timeout: 3)
        XCTAssertEqual(service.state, .idle)
        try assertClean(fixture)
    }

    @MainActor
    func testTimeoutCancelsOnlyAfterRecognitionBegins() async throws {
        let fixture = try fixture(backend: cancellableBackend, capture: "#!/bin/sh\n/bin/sleep 0.2\nprintf 'image' > \"$3\"\n")
        let service = service(fixture, timeout: 0.1)
        var states: [OCRState] = []
        service.onStateChange = { states.append($0) }
        let result: Result<OCRResult, Error> = await withCheckedContinuation { continuation in
            service.capture(configuration: fixture.configuration) { continuation.resume(returning: $0) }
        }
        guard case .failure(OCRError.timeout) = result else { return XCTFail("Expected recognition timeout") }
        // Capture deliberately lasts longer than the recognition timeout. The
        // OCR interpreter may still be starting when SIGINT arrives, so this
        // test checks stage ordering rather than racing its signal handler.
        XCTAssertEqual(states, [.capturing, .recognizing, .cancelling, .idle])
        try assertClean(fixture)
    }

    @MainActor
    func testLaunchFailureCleansFilesAndReturnsToIdle() async throws {
        let fixture = try fixture(backend: "#!/bin/sh\nexit 0\n")
        let service = OCRService(captureExecutable: fixture.root.appendingPathComponent("missing executable"),
                                 recognitionTimeout: 1, temporaryDirectory: fixture.temporary)
        let result: Result<OCRResult, Error> = await withCheckedContinuation { continuation in
            service.capture(configuration: fixture.configuration) { continuation.resume(returning: $0) }
        }
        if case .success = result { XCTFail("Expected launch error") }
        XCTAssertEqual(service.state, .idle)
        try assertClean(fixture)
    }

    @MainActor
    func testMissingResultIsNotSuccessfulEmptyText() async throws {
        let fixture = try fixture(backend: "#!/bin/sh\nexit 0\n")
        let service = service(fixture)
        guard case .failure(OCRError.resultFile) = await recognize(service, fixture: fixture) else {
            return XCTFail("Expected missing result error")
        }
        try assertClean(fixture)
    }
}
