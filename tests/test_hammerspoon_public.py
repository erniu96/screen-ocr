"""Run isolated Lua lifecycle tests; no real UI, clipboard or files are changed.

uv run --with lupa python -m unittest discover -s tests -p test_hammerspoon_public.py -v
"""

from pathlib import Path
import unittest

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None


@unittest.skipIf(LuaRuntime is None, "use uv run --with lupa for Hammerspoon tests")
class HammerspoonTests(unittest.TestCase):
    def test_screenshot_clipboard_lifecycle(self):
        runtime = LuaRuntime(unpack_returned_tuples=True)
        runtime.globals().TEST_MODULE_PATH = str(
            Path(__file__).resolve().parents[1] / "integrations/hammerspoon/screen_ocr.lua"
        )
        result = runtime.execute(Path(__file__).with_suffix(".lua").read_text())
        self.assertEqual(result["passed"], 18)


if __name__ == "__main__":
    unittest.main()
