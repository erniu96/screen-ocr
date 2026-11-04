import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import plistlib
import tempfile
import unittest

from screen_ocr.runtime_assets import (
    app_version, compare_assets, load_baseline, main, validate_baseline, verify_assets,
)


def sample_baseline():
    return {
        "format_version": 1, "source_version": "4.2.0", "source_build": "270352",
        "architecture": "arm64", "source_url": "https://example.invalid/source.dmg",
        "source_dmg_sha256": "0" * 64,
        "files": [{"path": "Resources/model.xnet", "category": "model", "bytes": 3,
                   "sha256": hashlib.sha256(b"old").hexdigest()}],
    }


class BaselineTests(unittest.TestCase):
    def test_packaged_baseline_covers_all_twelve_extracted_files(self):
        baseline = load_baseline()
        self.assertEqual(len(baseline["files"]), 12)
        self.assertEqual(sum(item["bytes"] for item in baseline["files"]), 89229802)
        self.assertEqual([item["category"] for item in baseline["files"]].count("library"), 2)
        self.assertEqual([item["category"] for item in baseline["files"]].count("model"), 9)
        self.assertEqual([item["category"] for item in baseline["files"]].count("charset"), 1)
        self.assertFalse(any(item["path"].startswith("bin/") for item in baseline["files"]))

    def test_exact_schema_rejects_alternate_fields_and_bad_hashes(self):
        changes = (
            lambda record: record.update(format_version=2),
            lambda record: record.update(format_version=True),
            lambda record: record.update(version=record.pop("source_version")),
            lambda record: record["files"][0].update(size=record["files"][0].pop("bytes")),
            lambda record: record["files"][0].update(bytes=True),
            lambda record: record["files"][0].update(sha256="abc"),
            lambda record: record["files"].append(copy.deepcopy(record["files"][0])),
        )
        for change in changes:
            baseline = sample_baseline()
            change(baseline)
            with self.subTest(baseline=baseline), self.assertRaises(ValueError):
                validate_baseline(baseline)

    def test_untrusted_baseline_cannot_select_paths_outside_asset_directories(self):
        for path in ("../secret", "Resources/../secret", "Resources/../../secret", "/etc/passwd",
                     "Resources//model.xnet", "Resources/.", "Resources/..", "Resources/model\0.xnet"):
            baseline = sample_baseline()
            baseline["files"][0]["path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_baseline(baseline)


class InventoryTests(unittest.TestCase):
    def test_matching_changed_and_missing_bytes_are_distinct(self):
        baseline = sample_baseline()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Resources/model.xnet"
            path.parent.mkdir()
            path.write_bytes(b"old")
            matched = compare_assets(root, baseline)
            self.assertEqual(matched["summary"], {"unchanged": 1, "changed": 0, "missing": 0})
            self.assertTrue(matched["all_expected_files_match"])
            self.assertFalse(matched["compatibility_verified"])
            verify_assets(root, baseline)
            path.write_bytes(b"new")  # Same length still produces a changed SHA-256.
            changed = compare_assets(root, baseline)
            self.assertEqual(changed["files"][0]["status"], "changed")
            self.assertNotEqual(changed["files"][0]["actual_sha256"], baseline["files"][0]["sha256"])
            with self.assertRaisesRegex(ValueError, "model.xnet.*changed"):
                verify_assets(root, baseline)
            path.unlink()
            missing = compare_assets(root, baseline)
            self.assertEqual(missing["files"][0]["status"], "missing")
            self.assertIsNone(missing["files"][0]["actual_sha256"])
            with self.assertRaisesRegex(ValueError, "model.xnet.*missing"):
                verify_assets(root, baseline)

    def test_added_xnet_files_are_listed_without_mapping_them_to_old_names(self):
        baseline = sample_baseline()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Resources").mkdir()
            (root / "Resources/model-v2.xnet").write_bytes(b"old")
            result = compare_assets(root, baseline)
            self.assertEqual(result["summary"]["missing"], 1)
            self.assertEqual(result["additional_xnet_files"], [{
                "path": "Resources/model-v2.xnet", "bytes": 3,
                "sha256": baseline["files"][0]["sha256"],
            }])

    def test_asset_symlink_cannot_escape_inspected_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            root = directory / "runtime"
            (root / "Resources").mkdir(parents=True)
            target = directory / "outside.xnet"
            target.write_bytes(b"old")
            (root / "Resources/model.xnet").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "escapes"):
                compare_assets(root, sample_baseline())

    def test_application_metadata_uses_only_documented_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / "source.app"
            (app / "Contents").mkdir(parents=True)
            info = app / "Contents/Info.plist"
            info.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "9.0", "CFBundleVersion": "900"}))
            self.assertEqual(app_version(app), {"source_version": "9.0", "source_build": "900"})
            info.write_bytes(plistlib.dumps({"version": "9.0", "build": "900"}))
            with self.assertRaises(KeyError):
                app_version(app)

    def test_cli_inspection_is_read_only_and_differences_exit_one(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = root / "runtime"
            (runtime / "Resources").mkdir(parents=True)
            model = runtime / "Resources/model.xnet"
            model.write_bytes(b"new")
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps(sample_baseline()))
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = main(["--runtime", str(runtime), "--baseline", str(baseline)])
            self.assertEqual(status, 1)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["candidate"], {
                "kind": "runtime", "source_version": None, "source_build": None, "dmg_sha256": None,
            })
            self.assertEqual(report["files"][0]["status"], "changed")
            after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(before, after)

    def test_future_application_version_can_be_inspected_without_accepting_it_for_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = root / "candidate.app"
            (app / "Contents/Resources").mkdir(parents=True)
            (app / "Contents/Resources/model.xnet").write_bytes(b"old")
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
                "CFBundleShortVersionString": "9.0", "CFBundleVersion": "900",
            }))
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps(sample_baseline()))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = main(["--app", str(app), "--baseline", str(baseline)])
            self.assertEqual(status, 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["candidate"]["source_version"], "9.0")
            self.assertEqual(report["baseline"]["source_version"], "4.2.0")
            self.assertTrue(report["all_expected_files_match"])
            self.assertFalse(report["compatibility_verified"])


if __name__ == "__main__":
    unittest.main()
