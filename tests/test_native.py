"""Opt-in tests against the real, locally prepared macOS WeChat runtime.

Run with SCREEN_OCR_RUN_NATIVE=1 uv run python -m unittest discover -s tests -v.
The fixture images are generated samples, with no user data.
"""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from screen_ocr.engine import EngineTimeout, NativeEngine, NativeError


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
RUNTIME = ROOT / ".runtime"
EXPECTED_LINES = ["WeChat OCR native test", "Invoice 12345 ABC xyz"]


class ProcessCleanupTests(unittest.TestCase):
    def test_permission_failure_with_live_process_is_not_ignored(self):
        worker = Mock(pid=123)
        with patch("screen_ocr.engine.os.killpg", side_effect=PermissionError("denied")):
            with patch("screen_ocr.engine.subprocess.check_output", return_value="  123 S\n"):
                with self.assertRaises(PermissionError):
                    NativeEngine._terminate(worker)
        worker.wait.assert_not_called()


@unittest.skipUnless(os.environ.get("SCREEN_OCR_RUN_NATIVE") == "1", "set SCREEN_OCR_RUN_NATIVE=1 for native integration")
class NativeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = NativeEngine(RUNTIME)
        self.addCleanup(self.engine.close)

    def assert_success(self, result):
        self.assertEqual(set(result), {"imgpath", "errcode", "width", "height", "ocr_response"})
        self.assertEqual(result["errcode"], 0)
        self.assertEqual((result["width"], result["height"]), (1000, 260))
        self.assertEqual(result["imgpath"], str((FIXTURES / "english.png").resolve()))
        self.assertEqual([line["text"] for line in result["ocr_response"]], EXPECTED_LINES)
        for line in result["ocr_response"]:
            self.assertEqual(set(line), {"text", "rate", "left", "top", "right", "bottom"})
            self.assertGreater(line["rate"], 0.9)
            self.assertLessEqual(line["rate"], 1)
            self.assertLess(line["left"], line["right"])
            self.assertLess(line["top"], line["bottom"])
            self.assertGreaterEqual(line["left"], 0)
            self.assertGreaterEqual(line["top"], 0)
            self.assertLessEqual(line["right"], result["width"])
            self.assertLessEqual(line["bottom"], result["height"])

    def assert_process_group_gone(self, group):
        # A killed helper can briefly remain a zombie until macOS reaps it.
        # Inspect only the exact process group created by this test.
        deadline = time.monotonic() + 3
        while True:
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                return
            if time.monotonic() >= deadline:
                self.fail(f"native process group {group} survived cleanup")
            time.sleep(0.02)

    def test_recognizes_real_image_and_returns_boxes(self):
        self.assert_success(self.engine.recognize(FIXTURES / "english.png"))

    def test_recognizes_chinese_and_mixed_text(self):
        result = self.engine.recognize(FIXTURES / "chinese.png")
        self.assertEqual(result["errcode"], 0)
        self.assertEqual((result["width"], result["height"]), (1000, 300))
        self.assertEqual(
            [line["text"] for line in result["ocr_response"]],
            ["微信文字识别测试", "macOS 原生 OCR", "订单金额123.45元"],
        )

    def test_zombie_only_process_group_is_reaped(self):
        worker = subprocess.Popen(
            [sys.executable, "-c", "import os; os._exit(0)"], start_new_session=True,
        )
        self.addCleanup(worker.wait)
        deadline = time.monotonic() + 5
        # Do not call poll/wait yet: retain the zombie to reproduce macOS's
        # EPERM response for a process group containing no live processes.
        while True:
            state = subprocess.check_output(
                ["/bin/ps", "-p", str(worker.pid), "-o", "stat="], text=True,
            ).strip()
            if state.startswith("Z"):
                break
            if time.monotonic() >= deadline:
                worker.kill()
                self.fail("test child did not become a zombie")
            time.sleep(0.02)
        with self.assertRaises(PermissionError):
            os.killpg(worker.pid, signal.SIGKILL)
        NativeEngine._terminate(worker)
        self.assertEqual(worker.returncode, 0)
        self.assert_process_group_gone(worker.pid)

    def test_blank_image_is_successful_empty_result(self):
        result = self.engine.recognize(FIXTURES / "blank.png")
        self.assertEqual(result["errcode"], 0)
        self.assertEqual((result["width"], result["height"]), (320, 160))
        self.assertEqual(result["ocr_response"], [])

    def test_invalid_image_preserves_native_error(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.png"
            invalid.write_bytes(b"oops")
            result = self.engine.recognize(invalid)
        # Observed from the pinned native runtime: image decode failure is 6.
        self.assertEqual(result["errcode"], 6)
        self.assertEqual((result["width"], result["height"]), (0, 0))
        self.assertEqual(result["ocr_response"], [])

    def test_two_concurrent_workers_do_not_collide(self):
        barrier = threading.Barrier(2)

        def recognize():
            barrier.wait(timeout=5)
            return self.engine.recognize(FIXTURES / "english.png")

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(recognize) for _ in range(2)]
            for future in futures:
                self.assert_success(future.result(timeout=100))

    def test_timeout_reaps_worker_and_allows_next_request(self):
        workers = []
        launch = subprocess.Popen

        def record_launch(*args, **kwargs):
            worker = launch(*args, **kwargs)
            workers.append(worker)
            return worker

        self.engine.timeout = 0.001
        with patch("screen_ocr.engine.subprocess.Popen", side_effect=record_launch):
            with self.assertRaises(EngineTimeout):
                self.engine.recognize(FIXTURES / "english.png")
        self.assertEqual(len(workers), 1)
        self.assertIsNotNone(workers[0].returncode)
        self.assert_process_group_gone(workers[0].pid)
        self.engine.timeout = 90
        self.assert_success(self.engine.recognize(FIXTURES / "english.png"))

    def test_close_cancels_inflight_worker_without_leak(self):
        launch = subprocess.Popen
        spawned = threading.Event()
        workers = []

        def suspended_launch(*args, **kwargs):
            # Stop a real subprocess immediately so this tests cancellation
            # deterministically, without racing a subsecond OCR completion.
            worker = launch(*args, **kwargs)
            os.killpg(worker.pid, signal.SIGSTOP)
            workers.append(worker)
            spawned.set()
            return worker

        with patch("screen_ocr.engine.subprocess.Popen", side_effect=suspended_launch):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.engine.recognize, FIXTURES / "english.png")
                self.assertTrue(spawned.wait(5), "worker was not spawned")
                # close() acquires the same launch lock, so registration of
                # this worker finishes before close takes its active snapshot.
                self.engine.close()
                with self.assertRaises(NativeError):
                    future.result(timeout=5)
        self.assertEqual(len(workers), 1)
        self.assertIsNotNone(workers[0].returncode)
        self.assert_process_group_gone(workers[0].pid)
        with self.assertRaisesRegex(NativeError, "closed"):
            self.engine.recognize(FIXTURES / "english.png")


if __name__ == "__main__":
    unittest.main()
