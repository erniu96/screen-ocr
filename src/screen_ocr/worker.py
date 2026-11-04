"""One request per process keeps Mojo's global state and callbacks isolated."""

import argparse
import ctypes as c
import json
import os
from pathlib import Path
import threading
import time

from .protocol import decode_response, decode_support, encode_request

BUNDLE_ID = b"dev.screenocr.native"


class MojoSession:
    def __init__(self, runtime: Path):
        self.runtime = runtime
        self.ready = threading.Event()
        self.done = threading.Event()
        self.error: str | None = None
        self.result: dict | None = None
        self.closing = False
        self.library = c.CDLL(str(runtime / "Frameworks/libmmmojo.dylib"))
        self.callbacks: list = []  # ctypes callbacks must outlive native threads.
        self.env = None
        self._bind_functions()

    def _bind(self, name, result, args):
        function = getattr(self.library, name)
        function.restype = result
        function.argtypes = args
        return function

    def _bind_functions(self):
        self.configure = self._bind("SetMMMojoConfiguration", None, [c.c_int])
        self.initialize = self._bind("InitializeMMMojo", None, [c.c_int, c.POINTER(c.c_char_p)])
        self.create = self._bind("CreateMMMojoEnvironment", c.c_void_p, [])
        # On Apple ARM64, specifying the fixed arguments is essential for varargs.
        self.callback = self._bind("SetMMMojoEnvironmentCallbacks", None, [c.c_void_p, c.c_int])
        self.param = self._bind("SetMMMojoEnvironmentInitParams", None, [c.c_void_p, c.c_int])
        self.append = self._bind("AppendMMSubProcessSwitchNative", None, [c.c_void_p, c.c_char_p, c.c_char_p])
        self.start = self._bind("StartMMMojoEnvironment", None, [c.c_void_p])
        self.stop = self._bind("StopMMMojoEnvironment", None, [c.c_void_p])
        self.remove = self._bind("RemoveMMMojoEnvironment", None, [c.c_void_p])
        self.read = self._bind("GetMMMojoReadInfoRequest", c.c_void_p, [c.c_void_p, c.POINTER(c.c_uint32)])
        self.remove_read = self._bind("RemoveMMMojoReadInfo", None, [c.c_void_p])
        self.new_write = self._bind("CreateMMMojoWriteInfo", c.c_void_p, [c.c_int, c.c_bool, c.c_uint32])
        self.buffer = self._bind("GetMMMojoWriteInfoRequest", c.c_void_p, [c.c_void_p, c.c_size_t])
        self.send = self._bind("SendMMMojoWriteInfo", c.c_bool, [c.c_void_p, c.c_void_p])
        self.remove_write = self._bind("RemoveMMMojoWriteInfo", None, [c.c_void_p])

    def _fail(self, reason: str):
        if not self.closing and self.error is None:
            self.error = reason
            self.ready.set()
            self.done.set()

    def _push(self, request_id, info, _):
        handshake = False
        completed = False
        failure = None
        try:
            size = c.c_uint32()
            data = self.read(info, c.byref(size))
            if size.value > 64 * 1024 * 1024 or (size.value and not data):
                raise ValueError("Invalid native response buffer")
            payload = c.string_at(data, size.value)
            if request_id == 10001:
                if not decode_support(payload):
                    failure = "Native engine reports OCR unsupported"
                handshake = True
            elif request_id == 10011:
                task_id, result = decode_response(payload)
                if task_id != 2:
                    raise ValueError(f"Unexpected OCR task ID {task_id}")
                self.result = result
                completed = True
        except Exception as exc:
            failure = f"Invalid OCR response: {exc}"
        finally:
            self.remove_read(info)
        # Publish completion only after freeing the callback's native buffer;
        # the waiting thread may immediately stop and remove the environment.
        if failure is not None:
            self._fail(failure)
        if handshake:
            self.ready.set()
        if completed:
            self.done.set()

    def _register(self, kind, signature, function):
        callback = signature(function)
        self.callbacks.append(callback)
        self.callback(self.env, kind, c.cast(callback, c.c_void_p))

    def open(self):
        os.environ["SCREEN_OCR_RUNTIME"] = str(self.runtime / "Resources/libwxocr.dylib")
        self.configure(1000, c.c_char_p(BUNDLE_ID))
        self.initialize(0, None)
        self.env = self.create()
        if not self.env:
            raise RuntimeError("CreateMMMojoEnvironment returned null")
        self.callback(self.env, 0, c.c_void_p())
        self._register(1, c.CFUNCTYPE(None, c.c_uint32, c.c_void_p, c.c_void_p), self._push)
        # The pinned macOS SDK invokes connect/launch callbacks without null
        # checks. Keep both registered even though the protobuf handshake is
        # the event that marks the engine ready.
        self._register(4, c.CFUNCTYPE(None, c.c_bool, c.c_void_p), lambda connected, _: None)
        self._register(5, c.CFUNCTYPE(None, c.c_void_p), lambda _: self._fail("Native engine disconnected"))
        self._register(6, c.CFUNCTYPE(None, c.c_void_p), lambda _: None)
        self._register(7, c.CFUNCTYPE(None, c.c_int, c.c_void_p),
                       lambda code, _: self._fail(f"Native engine launch failed: {code}"))
        self._register(8, c.CFUNCTYPE(None, c.c_void_p, c.c_int, c.c_void_p),
                       lambda data, length, _: self._fail(c.string_at(data, length).decode("utf-8", errors="replace")))
        self.param(self.env, 0, c.c_int(1))
        self.param(self.env, 2, c.c_char_p(os.fsencode(self.runtime / "bin/screen-ocr-engine")))
        self.append(self.env, b"no-sandbox", b"")
        self.append(self.env, b"base-bundle-id", BUNDLE_ID)
        self.append(self.env, b"ocr-resource-root", os.fsencode(self.runtime / "Resources"))
        self.start(self.env)

    def wait_ready(self, timeout: float):
        if not self.ready.wait(timeout):
            raise TimeoutError("Native OCR handshake timed out")
        if self.error:
            raise RuntimeError(self.error)

    def recognize(self, image: Path, timeout: float) -> dict:
        self.wait_ready(timeout)
        payload = encode_request(2, str(image))
        info = self.new_write(1, False, 10010)
        if not info:
            raise RuntimeError("Cannot allocate Mojo request")
        target = self.buffer(info, len(payload))
        if not target:
            self.remove_write(info)
            raise RuntimeError("Cannot allocate Mojo payload")
        c.memmove(target, payload, len(payload))
        # Send transfers ownership of write_info to MMMojo.
        if not self.send(self.env, info):
            raise RuntimeError("SendMMMojoWriteInfo failed")
        if not self.done.wait(timeout):
            raise TimeoutError("Native OCR response timed out")
        if self.error:
            raise RuntimeError(self.error)
        if self.result is None:
            raise RuntimeError("Native engine returned no result")
        return {"imgpath": str(image), **self.result}

    def close(self):
        self.closing = True
        if self.env:
            self.stop(self.env)
            self.remove(self.env)
            self.env = None
        # Never dlclose a library with live native threads. Process exit reclaims it.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metrics-output", type=Path, help="Optional separate monotonic timing JSON output")
    parser.add_argument("--timeout", type=float, required=True)
    args = parser.parse_args()
    started_ns = time.perf_counter_ns()
    session = MojoSession(args.runtime)
    try:
        session.open()
        session.wait_ready(args.timeout)
        ready_ns = time.perf_counter_ns()
        result = session.recognize(args.image, args.timeout)
        recognized_ns = time.perf_counter_ns()
    finally:
        shutdown_started_ns = time.perf_counter_ns()
        session.close()
        finished_ns = time.perf_counter_ns()
    # Reaching here requires both recognition and cleanup to have completed.
    # Keep result.json unchanged; benchmark metadata has its own explicit file.
    args.output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    if args.metrics_output is not None:
        metrics = {
            "worker_startup_ms": (ready_ns - started_ns) / 1e6,
            "worker_recognition_ms": (recognized_ns - ready_ns) / 1e6,
            "worker_shutdown_ms": (finished_ns - shutdown_started_ns) / 1e6,
            "worker_total_ms": (finished_ns - started_ns) / 1e6,
        }
        args.metrics_output.write_text(json.dumps(metrics, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
