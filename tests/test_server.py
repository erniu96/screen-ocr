from __future__ import annotations

import base64
from contextlib import redirect_stderr
import http.client
import io
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

from screen_ocr.cli import main
from screen_ocr.engine import EngineTimeout, NativeError
from screen_ocr.server import MAX_BODY_BYTES, create_server


class FakeEngine:
    def __init__(self) -> None:
        self.paths: list[Path] = []
        self.images: list[bytes] = []
        self.error: Exception | None = None
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False

    def recognize(self, image: Path) -> dict:
        self.paths.append(image)
        self.images.append(image.read_bytes())
        self.started.set()
        if self.block and not self.release.wait(5):
            raise RuntimeError("test engine was not released")
        if self.error is not None:
            raise self.error
        return {
            "errcode": 0,
            "width": 100,
            "height": 50,
            "imgpath": str(image),
            "ocr_response": [{"text": "Hello", "rate": 0.9, "left": 1.0,
                              "top": 2.0, "right": 90.0, "bottom": 40.0}],
        }


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FakeEngine()
        self.server = create_server(self.engine, port=0, max_concurrent=1, request_timeout=1)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.valid_body = json.dumps({"image": base64.b64encode(b"test image bytes").decode()})

    def tearDown(self) -> None:
        self.engine.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, body: str, *, headers: dict | None = None, path: str = "/ocr") -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request("POST", path, body, headers or {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def assert_cleaned(self) -> None:
        for path in self.engine.paths:
            self.assertFalse(path.exists())
            self.assertFalse(path.parent.exists())

    def test_success_preserves_observed_response_and_cleans_image(self) -> None:
        status, body = self.request(self.valid_body)
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"result"})
        self.assertEqual(set(body["result"]), {"errcode", "width", "height", "imgpath", "ocr_response"})
        self.assertEqual(body["result"]["ocr_response"][0]["text"], "Hello")
        self.assertEqual(self.engine.images, [b"test image bytes"])
        self.assert_cleaned()

    def test_invalid_inputs_do_not_invoke_engine(self) -> None:
        for body in ("{", "null", "[]", "{}", '{"image":12}', '{"image":""}',
                     '{"image":"%%%"}', '{"image":"data:image/png;base64,YQ=="}'):
            with self.subTest(body=body):
                status, response = self.request(body)
                self.assertEqual(status, 400)
                self.assertEqual(set(response), {"error"})
        self.assertEqual(self.engine.paths, [])

    def test_body_limit_rejects_before_waiting_for_body(self) -> None:
        status, _ = self.request("{}", headers={"Content-Type": "application/json",
                                                "Content-Length": str(MAX_BODY_BYTES + 1)})
        self.assertEqual(status, 413)
        self.assertEqual(self.engine.paths, [])

    def test_engine_errors_have_status_and_cleanup_without_internal_details(self) -> None:
        for error, expected in ((EngineTimeout("private timeout details"), 504),
                                (NativeError("private runtime details"), 503),
                                (RuntimeError("private traceback details"), 500)):
            with self.subTest(error=error):
                self.engine.error = error
                status, response = self.request(self.valid_body)
                self.assertEqual(status, expected)
                self.assertNotIn("private", response["error"])
                self.assert_cleaned()

    def test_busy_service_does_not_start_second_recognition(self) -> None:
        self.engine.block = True
        result: list[tuple[int, dict]] = []
        caller = threading.Thread(target=lambda: result.append(self.request(self.valid_body)))
        caller.start()
        try:
            self.assertTrue(self.engine.started.wait(2))
            status, _ = self.request(self.valid_body)
            self.assertEqual(status, 503)
            self.assertEqual(len(self.engine.paths), 1)
        finally:
            self.engine.release.set()
            caller.join(4)
        self.assertEqual(result[0][0], 200)
        self.assert_cleaned()

    def test_only_exact_ocr_route_is_exposed(self) -> None:
        for path in ("/", "/ocr/", "/ocr?debug=1"):
            status, _ = self.request(self.valid_body, path=path)
            self.assertEqual(status, 404)
        self.assertEqual(self.engine.paths, [])

    def test_server_close_waits_for_request_cleanup(self) -> None:
        self.engine.block = True
        result: list[tuple[int, dict]] = []
        caller = threading.Thread(target=lambda: result.append(self.request(self.valid_body)))
        closed = threading.Event()

        def close_server() -> None:
            self.server.server_close()
            closed.set()

        closer = threading.Thread(target=close_server)
        caller.start()
        try:
            self.assertTrue(self.engine.started.wait(2))
            closer.start()
            self.assertFalse(closed.wait(0.1))
        finally:
            self.engine.release.set()
            caller.join(4)
            if closer.ident is not None:
                closer.join(4)
        self.assertTrue(closed.is_set())
        self.assertEqual(result[0][0], 200)
        self.assert_cleaned()


class CLIShutdownTests(unittest.TestCase):
    def test_ctrl_c_cancels_engine_before_waiting_for_http_threads(self) -> None:
        events: list[str] = []
        engine = Mock()
        engine.close.side_effect = lambda: events.append("engine closed")
        server = Mock(server_port=5000)
        server.serve_forever.side_effect = KeyboardInterrupt
        server.server_close.side_effect = lambda: events.append("server closed")
        with patch("screen_ocr.cli.NativeEngine", return_value=engine), \
             patch("screen_ocr.cli.create_server", return_value=server), \
             redirect_stderr(io.StringIO()):
            status = main(["serve"])
        self.assertEqual(status, 0)
        self.assertEqual(events, ["engine closed", "server closed"])

    def test_listening_failure_still_closes_engine(self) -> None:
        engine = Mock()
        with patch("screen_ocr.cli.NativeEngine", return_value=engine), \
             patch("screen_ocr.cli.create_server", side_effect=OSError("address already in use")), \
             redirect_stderr(io.StringIO()):
            status = main(["serve"])
        self.assertEqual(status, 1)
        engine.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
