"""Private timing schema checks and an opt-in real-engine measurement."""

import math
import os
from pathlib import Path
import unittest

from wxocr_native.engine import NativeEngine, NativeError, RecognitionOutcome, _validate_worker_timings


ROOT = Path(__file__).resolve().parents[1]
TIMING_FIELDS = {
    "wall_ms", "worker_startup_ms", "worker_recognition_ms", "worker_shutdown_ms", "worker_total_ms",
}


class TimingContractTests(unittest.TestCase):
    def test_missing_or_alternate_field_names_are_rejected(self):
        record = {
            "worker_startup_ms": 1.0, "worker_recognition_ms": 2.0,
            "worker_shutdown_ms": 0.1, "worker_total_ms": 3.1,
        }
        del record["worker_recognition_ms"]
        record["inference_ms"] = 2.0
        with self.assertRaises(NativeError):
            _validate_worker_timings(record)

    def test_invalid_measurements_are_rejected(self):
        for value in (-1, float("nan"), float("inf"), True, "1.2"):
            with self.subTest(value=value):
                record = {
                    "worker_startup_ms": value, "worker_recognition_ms": 2.0,
                    "worker_shutdown_ms": 0.1, "worker_total_ms": 3.1,
                }
                with self.assertRaises(NativeError):
                    _validate_worker_timings(record)


@unittest.skipUnless(os.environ.get("WXOCR_RUN_NATIVE") == "1", "set WXOCR_RUN_NATIVE=1 for native integration")
class NativeTimingTests(unittest.TestCase):
    def test_real_engine_timings_leave_ocr_contract_unchanged(self):
        engine = NativeEngine(ROOT / ".runtime")
        try:
            outcome = engine.recognize_detailed(ROOT / "tests/fixtures/chinese.png")
        finally:
            engine.close()
        self.assertIsInstance(outcome, RecognitionOutcome)
        self.assertEqual(outcome.result["errcode"], 0)
        self.assertEqual(set(outcome.result), {"imgpath", "errcode", "width", "height", "ocr_response"})
        self.assertEqual(outcome.result["ocr_response"][0]["text"], "微信文字识别测试")
        self.assertEqual(set(outcome.timings), TIMING_FIELDS)
        self.assertTrue(all(math.isfinite(value) and value >= 0 for value in outcome.timings.values()))
        self.assertGreater(outcome.timings["worker_startup_ms"], 0)
        self.assertGreater(outcome.timings["worker_recognition_ms"], 0)
        self.assertGreaterEqual(outcome.timings["wall_ms"], outcome.timings["worker_total_ms"])
        phases = sum(outcome.timings[key] for key in (
            "worker_startup_ms", "worker_recognition_ms", "worker_shutdown_ms",
        ))
        self.assertGreaterEqual(outcome.timings["worker_total_ms"], phases)


if __name__ == "__main__":
    unittest.main()
