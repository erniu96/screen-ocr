import contextlib
import io
from pathlib import Path
import signal
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

from screen_ocr import cli


class CLIOutputTests(unittest.TestCase):
    def test_large_unicode_text_file_is_private_and_does_not_fill_stdout(self):
        text = "中文 mixed text 🙂\n" * 20000
        engine = Mock()
        engine.recognize.return_value = {"errcode": 0}
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "input.png"
            image.touch()
            output = Path(folder) / "识别 text.txt"
            stdout = io.StringIO()
            with patch.object(cli, "NativeEngine", return_value=engine), \
                 patch.object(cli, "reconstruct_text", return_value={"text": text}), \
                 contextlib.redirect_stdout(stdout):
                self.assertEqual(cli.main(["recognize", str(image), "--text-file", str(output)]), 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(output.read_text(encoding="utf-8"), text)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        engine.close.assert_called_once_with()

    def test_existing_output_is_never_overwritten(self):
        engine = Mock()
        engine.recognize.return_value = {"errcode": 0}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "existing.txt"
            output.write_text("keep me")
            with patch.object(cli, "NativeEngine", return_value=engine), \
                 patch.object(cli, "reconstruct_text", return_value={"text": "new text"}), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["recognize", str(output), "--text-file", str(output)]), 1)
            self.assertEqual(output.read_text(), "keep me")
        engine.close.assert_called_once_with()


class CLICancellationTests(unittest.TestCase):
    def test_both_signals_reap_engine_and_protect_cleanup_from_repeated_signals(self):
        signals = (signal.SIGINT, signal.SIGTERM)
        previous = {number: signal.getsignal(number) for number in signals}
        for number in signals:
            with self.subTest(signal=number), tempfile.NamedTemporaryFile() as image:
                engine = Mock()

                def interrupt(_image):
                    signal.getsignal(number)(number, None)

                def cleanup():
                    for item in signals:
                        self.assertEqual(signal.getsignal(item), signal.SIG_IGN)

                engine.recognize.side_effect = interrupt
                engine.close.side_effect = cleanup
                with patch.object(cli, "NativeEngine", return_value=engine):
                    self.assertEqual(cli.main(["recognize", image.name]), 128 + number)
                engine.close.assert_called_once_with()
                for item in signals:
                    self.assertEqual(signal.getsignal(item), previous[item])


if __name__ == "__main__":
    unittest.main()
