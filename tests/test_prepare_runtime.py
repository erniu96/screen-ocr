"""Runtime reuse verifies source bytes and rebuilds only our local helper."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from screen_ocr.runtime_assets import load_baseline


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_runtime.py"
SPEC = importlib.util.spec_from_file_location("prepare_runtime", SCRIPT)
prepare_runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_runtime)


class RuntimeReuseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "old-runtime"
        self.output = self.root / "new-runtime"
        self.baseline = copy.deepcopy(load_baseline())
        for item in self.baseline["files"]:
            path = self.source / item["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            data = ("test asset: " + item["path"]).encode()
            path.write_bytes(data)
            item["bytes"] = len(data)
            item["sha256"] = hashlib.sha256(data).hexdigest()
        # Reuse must verify bytes, not trust a prior helper or manifest.
        (self.source / "runtime.json").write_text("invalid old manifest")
        self.patch_baseline = patch.object(prepare_runtime, "BASELINE", self.baseline)
        self.patch_baseline.start()
        self.addCleanup(self.patch_baseline.stop)

    @staticmethod
    def compile_helper(command, **_kwargs):
        Path(command[command.index("-o") + 1]).write_bytes(b"new helper")

    def test_cli_reuses_verified_assets_without_network_or_old_manifest(self):
        arguments = [str(SCRIPT), "--runtime", str(self.source), "--output", str(self.output)]
        with patch("sys.argv", arguments), \
             patch.object(prepare_runtime.platform, "system", return_value="Darwin"), \
             patch.object(prepare_runtime.platform, "machine", return_value="arm64"), \
             patch.object(prepare_runtime.subprocess, "run", side_effect=self.compile_helper) as compiler, \
             patch.object(prepare_runtime.urllib.request, "urlopen", side_effect=AssertionError("network called")), \
             contextlib.redirect_stdout(io.StringIO()):
            prepare_runtime.main()
        compiler.assert_called_once()
        manifest = json.loads((self.output / "runtime.json").read_text())
        for item in self.baseline["files"]:
            self.assertEqual((self.output / item["path"]).read_bytes(), (self.source / item["path"]).read_bytes())
            self.assertEqual(manifest["sha256"][item["path"]], item["sha256"])
        self.assertEqual((self.output / "bin/screen-ocr-engine").read_bytes(), b"new helper")
        self.assertEqual((self.source / "runtime.json").read_text(), "invalid old manifest")

    def test_changed_or_missing_model_is_rejected_before_output_creation(self):
        item = next(item for item in self.baseline["files"] if item["category"] == "model")
        model = self.source / item["path"]
        original = model.read_bytes()
        for state in ("changed", "missing"):
            with self.subTest(state=state):
                if state == "changed":
                    model.write_bytes(b"x" * len(original))
                else:
                    model.unlink()
                with patch.object(prepare_runtime.subprocess, "run") as compiler:
                    with self.assertRaisesRegex(ValueError, "Unrecognized runtime assets"):
                        prepare_runtime.prepare_contents(self.source, self.output)
                    compiler.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_existing_destination_is_preserved(self):
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("keep")
        with patch.object(prepare_runtime.subprocess, "run") as compiler:
            with self.assertRaisesRegex(ValueError, "Runtime already exists"):
                prepare_runtime.prepare_contents(self.source, self.output)
            compiler.assert_not_called()
        self.assertEqual(marker.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
