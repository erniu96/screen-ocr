import contextlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

from screen_ocr.model_metadata import inspect_charset, inspect_xnet, main


def uint32(value):
    return struct.pack("<I", value)


def prefix(metadata=b"", count=3, name=b"Conv_0"):
    result = b"XNET" + uint32(26) + uint32(len(metadata)) + metadata + uint32(count)
    if count:
        result += uint32(len(name)) + name
    return result


class ModelMetadataTests(unittest.TestCase):
    def inspect(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.xnet"
            path.write_bytes(data)
            return inspect_xnet(path)

    def test_confirmed_prefix_and_nonempty_binary_metadata(self):
        result = self.inspect(prefix(b"\xff\x00\x80"))
        self.assertEqual(result["status"], "prefix_parsed")
        self.assertEqual(result["format_version"], 26)
        self.assertEqual(result["metadata_field_bytes"], 3)
        self.assertEqual(result["metadata_field_hex"], "ff0080")
        self.assertEqual(result["operator_count"], 3)
        self.assertEqual(result["first_operator_name"], "Conv_0")

    def test_empty_operator_list_has_no_name(self):
        result = self.inspect(prefix(count=0))
        self.assertEqual(result["status"], "prefix_parsed")
        self.assertIsNone(result["first_operator_name"])
        self.assertIsNone(result["first_operator_name_bytes"])

    def test_unknown_versions_are_not_interpreted(self):
        result = self.inspect(b"XNET" + uint32(27) + b"unrelated schema")
        self.assertEqual(result["status"], "unsupported_version")
        self.assertNotIn("metadata_field_bytes", result)
        self.assertNotIn("operator_count", result)

    def test_wrong_magic_and_short_header(self):
        for data in (b"", b"XNET", b"NOPE" + uint32(26)):
            with self.subTest(data=data):
                self.assertEqual(self.inspect(data)["status"], "unrecognized_header")

    def test_every_truncated_prefix_is_rejected(self):
        data = prefix(b"meta")
        for length in range(8, len(data)):
            with self.subTest(length=length):
                self.assertEqual(self.inspect(data[:length])["status"], "invalid_prefix")

    def test_untrusted_lengths_do_not_allocate_the_declared_size(self):
        result = self.inspect(b"XNET" + uint32(26) + uint32(0xFFFFFFFF))
        self.assertEqual(result["status"], "invalid_prefix")

    def test_large_strings_are_skipped_with_lengths_reported(self):
        result = self.inspect(prefix(b"x" * 4097, name=b"y" * 4097))
        self.assertEqual(result["status"], "prefix_parsed")
        self.assertEqual(result["metadata_field_bytes"], 4097)
        self.assertIsNone(result["metadata_field_hex"])
        self.assertEqual(result["first_operator_name_bytes"], 4097)
        self.assertIsNone(result["first_operator_name"])

    def test_invalid_utf8_name_is_not_silently_replaced(self):
        self.assertEqual(self.inspect(prefix(name=b"\xff"))["status"], "invalid_prefix")

    def test_character_table_preserves_space_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "charset.txt"
            path.write_text("中\nA\n \n", encoding="utf-8")
            result = inspect_charset(path)
        self.assertEqual(result["entry_count"], 3)
        self.assertEqual(result["unique_entry_count"], 3)
        self.assertTrue(result["all_entries_single_codepoint"])
        self.assertEqual(result["last_entry"], " ")

    def test_cli_returns_error_status_but_reports_all_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "good.xnet").write_bytes(prefix())
            (root / "new.xnet").write_bytes(b"XNET" + uint32(99))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([directory]), 1)
            report = json.loads(output.getvalue())
        self.assertEqual(len(report["models"]), 2)
        self.assertEqual(report["models"][0]["metadata"]["status"], "prefix_parsed")
        self.assertEqual(report["models"][1]["metadata"]["status"], "unsupported_version")
        self.assertTrue(all(len(item["sha256"]) == 64 for item in report["models"]))


@unittest.skipUnless(os.environ.get("SCREEN_OCR_RUN_NATIVE") == "1", "set SCREEN_OCR_RUN_NATIVE=1 for local model samples")
class ModelSampleTests(unittest.TestCase):
    def test_verified_model_prefixes_and_character_table(self):
        resources = Path(__file__).resolve().parents[1] / ".runtime/Resources"
        counts = {
            "text_det_fp16_v2.xnet": 158, "text_rec_zh13562_fp16_v2.xnet": 94,
            "paragraph_det_fp16_v2.xnet": 97, "QBarDetV1.7.0.26.xnet": 177,
            "QBarSegV1.1.0.26.xnet": 78, "DeepSR_REPSR_R_M4C16_Flip_ep24.xnet": 11,
            "FPMMFG1V1.2.0.26.xnet": 69, "FPMMFG2V1.3.1.26.xnet": 105,
            "PrivacyDetectV8.0.26.xnet": 322,
        }
        for filename, count in counts.items():
            with self.subTest(filename=filename):
                result = inspect_xnet(resources / filename)
                self.assertEqual(result["status"], "prefix_parsed")
                self.assertEqual(result["format_version"], 26)
                self.assertEqual(result["metadata_field_bytes"], 0)
                self.assertEqual(result["metadata_field_hex"], "")
                self.assertEqual(result["operator_count"], count)
                self.assertTrue(result["first_operator_name"])
        charset = inspect_charset(resources / "charset_zh13562.txt")
        self.assertEqual(charset["bytes"], 54258)
        self.assertEqual(charset["entry_count"], 13563)
        self.assertEqual(charset["unique_entry_count"], 13563)
        self.assertTrue(charset["all_entries_single_codepoint"])
        self.assertEqual(charset["last_entry"], " ")


if __name__ == "__main__":
    unittest.main()
