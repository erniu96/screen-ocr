"""Command line entry point for macOS-native WeChat OCR."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import signal
import sys

from .engine import NativeEngine, NativeError
from .layout import reconstruct_text
from .server import create_server


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return timeout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run WeChat OCR natively on macOS")
    commands = parser.add_subparsers(dest="command", required=True)
    recognize = commands.add_parser("recognize", help="Recognize a local image")
    recognize.add_argument("image", type=Path)
    text_output = recognize.add_mutually_exclusive_group()
    text_output.add_argument("--text", action="store_true",
                             help="Print text in visual-row order, stitching adjacent fragments")
    text_output.add_argument("--text-file", type=Path,
                             help="Write reconstructed UTF-8 text to a new file instead of stdout")
    serve = commands.add_parser("serve", help="Serve the POST /ocr JSON API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5000)
    serve.add_argument("--max-concurrent", type=int, choices=(1, 2), default=2)
    for command in (recognize, serve):
        command.add_argument("--runtime", type=Path, default=Path(".runtime"))
        command.add_argument(
            "--timeout", type=_positive_timeout, default=90.0,
            help="Native worker deadline in seconds, including initialization (default: 90)",
        )
    return parser


def _run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        engine = NativeEngine(args.runtime.expanduser().resolve(), timeout=args.timeout)
        server = None
        try:
            if args.command == "recognize":
                image = args.image.expanduser().resolve(strict=True)
                if not image.is_file():
                    raise ValueError("image must be a regular file")
                result = engine.recognize(image)
                if args.text or args.text_file is not None:
                    if result["errcode"] != 0:
                        raise ValueError(f"OCR failed: errcode={result['errcode']}")
                    text = reconstruct_text(result)["text"]
                    if args.text_file is not None:
                        # Large OCR output can fill GUI task pipes. Use a new,
                        # private file and never overwrite an existing path.
                        fd = os.open(args.text_file.expanduser(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(fd, "w", encoding="utf-8") as output:
                            output.write(text)
                    else:
                        print(text)
                else:
                    print(json.dumps({"result": result}, ensure_ascii=False, allow_nan=False))
                return 0
            server = create_server(
                engine,
                args.host,
                args.port,
                max_concurrent=args.max_concurrent,
                request_timeout=30.0,
            )
            print(f"wxocr-native listening on http://{args.host}:{server.server_port}", file=sys.stderr)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
        finally:
            # Cancel native workers before joining their request threads. This
            # also covers Ctrl-C and failures while creating the listening socket.
            try:
                engine.close()
            finally:
                if server is not None:
                    server.server_close()
        return 0
    except KeyboardInterrupt:
        return 130
    except (NativeError, OSError, ValueError) as exc:
        print(f"wxocr-native: {exc}", file=sys.stderr)
        return 1


class _Cancellation(BaseException):
    def __init__(self, number: int):
        self.number = number


def main(argv: list[str] | None = None) -> int:
    """Handle GUI cancellation as well as Ctrl-C without interrupting cleanup."""
    cancellation_signals = (signal.SIGINT, signal.SIGTERM)
    previous = {number: signal.getsignal(number) for number in cancellation_signals}

    def cancel(number, _frame):
        # A GUI reload may send SIGINT followed immediately by SIGTERM. Once
        # cancellation starts, allow finally blocks to reap all native children.
        for item in cancellation_signals:
            signal.signal(item, signal.SIG_IGN)
        raise _Cancellation(number)

    try:
        for number in cancellation_signals:
            signal.signal(number, cancel)
        return _run(argv)
    except _Cancellation as exc:
        return 128 + exc.number
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    raise SystemExit(main())
