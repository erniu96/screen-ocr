"""Opt-in native Swift worker lifecycle tests; Python is only the test driver.

Build ScreenOCRWorker first, then run:
SCREEN_OCR_RUN_SWIFT_NATIVE=1 uv run python -m unittest discover -s tests -p test_swift_worker.py -v
"""

import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(os.environ.get("SCREEN_OCR_SWIFT_WORKER", ROOT / "apps/macos/.build/debug/ScreenOCRWorker"))
RUNTIME = ROOT / ".runtime"
FIXTURES = ROOT / "tests/fixtures"


@unittest.skipUnless(os.environ.get("SCREEN_OCR_RUN_SWIFT_NATIVE") == "1", "set SCREEN_OCR_RUN_SWIFT_NATIVE=1 for Swift native integration")
class SwiftWorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="screen-ocr-swift-check-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.text = self.root / "text.txt"
        self.json = self.root / "result.json"

    def command(self, *, runtime=RUNTIME, image=FIXTURES / "english.png", timeout=5):
        return [str(WORKER), "--runtime", str(runtime), "--image", str(image),
                "--text-file", str(self.text), "--timeout", str(timeout)]

    def assert_no_group_members(self, group):
        deadline = time.monotonic() + 3
        while True:
            rows = subprocess.check_output(["/bin/ps", "-axo", "pgid=,stat="], text=True).splitlines()
            alive = [row for row in rows if int(row.split()[0]) == group and not row.split()[1].startswith("Z")]
            if not alive:
                return
            if time.monotonic() >= deadline:
                self.fail(f"Worker group {group} left live members: {alive}")
            time.sleep(0.02)

    def run_worker(self, command):
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=8)
            except BaseException:
                try:
                    if os.getpgid(process.pid) == process.pid:
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                process.wait()
                raise
        self.assert_no_group_members(process.pid)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def fake_runtime(self, script):
        runtime = self.root / "runtime"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "Frameworks").symlink_to(RUNTIME / "Frameworks", target_is_directory=True)
        (runtime / "Resources").symlink_to(RUNTIME / "Resources", target_is_directory=True)
        (runtime / "runtime.json").write_bytes((RUNTIME / "runtime.json").read_bytes())
        helper = runtime / "bin/screen-ocr-engine"
        helper.write_text("#!/bin/sh\n" + script)
        helper.chmod(0o700)
        return runtime

    def test_real_result_and_diagnostic_contract(self):
        completed = self.run_worker(self.command() + ["--json-file", str(self.json)])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.text.read_text(), "WeChat OCR native test\nInvoice 12345 ABC xyz")
        self.assertEqual(self.text.stat().st_mode & 0o777, 0o600)
        result = json.loads(self.json.read_text())
        self.assertEqual(set(result), {"result", "layout", "timings"})
        self.assertEqual(result["result"]["imgpath"], str(FIXTURES / "english.png"))
        self.assertEqual(result["result"]["errcode"], 0)
        self.assertEqual(result["layout"]["text"], self.text.read_text())
        self.assertEqual(set(result["timings"]), {"worker_startup_ms", "worker_recognition_ms", "worker_shutdown_ms", "worker_total_ms"})

    def test_blank_image_writes_empty_text(self):
        completed = self.run_worker(self.command(image=FIXTURES / "blank.png"))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.text.read_bytes(), b"")

    def test_invalid_image_fails_without_output(self):
        image = self.root / "invalid.png"
        image.write_bytes(b"not an image")
        completed = self.run_worker(self.command(image=image))
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("OCR engine returned error 6", completed.stderr)
        self.assertFalse(self.text.exists())

    def test_missing_runtime_fails_without_output(self):
        completed = self.run_worker(self.command(runtime=self.root / "missing"))
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(self.text.exists())

    def test_existing_output_is_never_overwritten(self):
        self.text.write_text("keep existing text")
        completed = self.run_worker(self.command())
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self.text.read_text(), "keep existing text")

    def test_output_symlink_is_never_followed(self):
        target = self.root / "keep.txt"
        target.write_text("keep symlink target")
        self.text.symlink_to(target)
        completed = self.run_worker(self.command())
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(target.read_text(), "keep symlink target")
        self.assertTrue(self.text.is_symlink())

    def test_helper_failure_leaves_no_processes(self):
        runtime = self.fake_runtime("exit 7\n")
        completed = self.run_worker(self.command(runtime=runtime, timeout=0.25))
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(self.text.exists())

    def test_handshake_timeout_reaps_sleeping_helper_and_descendant(self):
        runtime = self.fake_runtime("sleep 30\n")
        started = time.monotonic()
        completed = self.run_worker(self.command(runtime=runtime, timeout=0.1))
        self.assertNotEqual(completed.returncode, 0)
        self.assertLess(time.monotonic() - started, 4)
        self.assertFalse(self.text.exists())

    def test_sigint_reaps_sleeping_helper_and_descendant(self):
        marker = self.root / "helper-started"
        runtime = self.fake_runtime(f"echo ready > '{marker}'\nsleep 30\n")
        with subprocess.Popen(self.command(runtime=runtime), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                deadline = time.monotonic() + 3
                while not marker.exists():
                    self.assertIsNone(process.poll(), "Worker exited before helper startup")
                    self.assertLess(time.monotonic(), deadline, "Helper did not start")
                    time.sleep(0.01)
                self.assertEqual(os.getpgid(process.pid), process.pid)
                process.send_signal(signal.SIGINT)
                process.communicate(timeout=4)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(self.text.exists())
        self.assert_no_group_members(process.pid)


if __name__ == "__main__":
    unittest.main()
