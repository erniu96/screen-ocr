"""A bounded local HTTP adapter for the observed wxocr image contract."""
from __future__ import annotations

import base64
import binascii
import json
import math
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

from .engine import EngineTimeout, NativeError

MAX_BODY_BYTES = 20 * 1024 * 1024


class Recognizer(Protocol):
    def recognize(self, image: Path) -> dict[str, Any]: ...


class OCRHTTPServer(ThreadingHTTPServer):
    # CLI closes the native engine first, then joins handlers so temporary files
    # and cancelled subprocesses are reaped before the application exits.
    daemon_threads = False

    def __init__(
        self,
        address: tuple[str, int],
        engine: Recognizer,
        max_concurrent: int,
        request_timeout: float,
    ) -> None:
        self.engine = engine
        self.capacity = threading.BoundedSemaphore(max_concurrent)
        self.request_timeout = request_timeout
        super().__init__(address, OCRRequestHandler)


class OCRRequestHandler(BaseHTTPRequestHandler):
    server: OCRHTTPServer
    server_version = "wxocr-native"
    sys_version = ""
    # One request per connection bounds idle connection lifetime and avoids
    # reusing connections after rejecting an unread body.
    protocol_version = "HTTP/1.0"

    def setup(self) -> None:
        self.request.settimeout(self.server.request_timeout)
        super().setup()

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.close_connection = True
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            # The caller may have timed out while native inference was running.
            pass

    def do_GET(self) -> None:
        self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/ocr":
            self._json(404, {"error": "Not found"})
            return
        if not self.server.capacity.acquire(blocking=False):
            self._json(503, {"error": "OCR service is busy"})
            return
        try:
            self._recognize()
        except socket.timeout:
            self._json(408, {"error": "Request body timed out"})
        except EngineTimeout:
            self._json(504, {"error": "OCR engine timed out"})
        except NativeError:
            self._json(503, {"error": "OCR engine unavailable"})
        except Exception:
            self._json(500, {"error": "OCR processing failed"})
        finally:
            self.server.capacity.release()

    def _recognize(self) -> None:
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") is not None:
            self._json(400, {"error": "Transfer-Encoding is not supported"})
            return
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            self._json(400, {"error": "A valid Content-Length is required"})
            return
        # Compare digit count before int() so maliciously long integers cannot
        # exceed Python's conversion limit or consume unnecessary work.
        length_text = lengths[0].lstrip("0") or "0"
        if len(length_text) > len(str(MAX_BODY_BYTES)):
            self._json(413, {"error": "Request exceeds 20 MiB"})
            return
        length = int(length_text)
        if length > MAX_BODY_BYTES:
            self._json(413, {"error": "Request exceeds 20 MiB"})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(400, {"error": "Content-Type must be application/json"})
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self._json(400, {"error": "Incomplete request body"})
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError):
            self._json(400, {"error": "Invalid JSON body"})
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("image"), str):
            self._json(400, {"error": "image must be a base64 string"})
            return
        if not payload["image"]:
            self._json(400, {"error": "No image data provided"})
            return
        try:
            image_data = base64.b64decode(payload["image"], validate=True)
        except (ValueError, binascii.Error):
            self._json(400, {"error": "image must contain valid bare base64"})
            return
        with tempfile.TemporaryDirectory(prefix="wxocr-native-") as folder:
            image_path = Path(folder) / "input.png"
            image_path.write_bytes(image_data)
            result = self.server.engine.recognize(image_path)
        self._json(200, {"result": result})


def create_server(
    engine: Recognizer,
    host: str = "127.0.0.1",
    port: int = 5000,
    *,
    max_concurrent: int = 2,
    request_timeout: float = 30.0,
) -> OCRHTTPServer:
    """Create a server; the caller owns serve_forever(), shutdown(), and close."""
    if max_concurrent not in (1, 2):
        raise ValueError("max_concurrent must be 1 or 2")
    if not math.isfinite(request_timeout) or request_timeout <= 0:
        raise ValueError("request_timeout must be finite and positive")
    return OCRHTTPServer((host, port), engine, max_concurrent, request_timeout)
