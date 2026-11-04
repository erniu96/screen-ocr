"""Isolate the proprietary native runtime and enforce a whole-request deadline."""

import json
from dataclasses import dataclass
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import threading
import time


class NativeError(RuntimeError):
    """The local engine failed to initialize or complete a request."""


class EngineTimeout(NativeError):
    """The worker exceeded its deadline and its process group was terminated."""


@dataclass(frozen=True)
class RecognitionOutcome:
    """OCR output plus independently measured host and native-worker durations.

    All timings are milliseconds measured with a monotonic clock. ``wall_ms``
    covers the whole recognize_detailed call, including temporary files,
    process launch, native work, teardown and result decoding. Worker timings
    exclude Python interpreter/import startup and output-file serialization:
    startup ends after the OCR-support handshake; recognition covers request
    construction, IPC, inference and response decoding; shutdown covers native
    Stop/Remove calls. ``worker_total_ms`` covers these three worker phases.
    The proprietary engine's time_used field is deliberately not interpreted.
    """

    result: dict
    timings: dict[str, float]


_WORKER_TIMING_FIELDS = frozenset({
    "worker_startup_ms", "worker_recognition_ms", "worker_shutdown_ms", "worker_total_ms",
})


def _validate_worker_timings(value: object) -> dict[str, float]:
    """Require the exact private worker metrics contract, never infer aliases."""
    if not isinstance(value, dict) or value.keys() != _WORKER_TIMING_FIELDS:
        raise NativeError("Native worker produced an invalid timing schema")
    if any(type(item) not in (int, float) or not math.isfinite(item) or item < 0 for item in value.values()):
        raise NativeError("Native worker produced an invalid timing value")
    return {name: float(value[name]) for name in _WORKER_TIMING_FIELDS}


class NativeEngine:
    def __init__(self, runtime: Path, timeout: float = 90):
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise NativeError("This build requires macOS on Apple Silicon")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.runtime = Path(runtime).resolve()
        self.timeout = timeout
        self._lock = threading.Lock()
        self._active: set[subprocess.Popen] = set()
        self._closed = False
        for name in ("bin/screen-ocr-engine", "Frameworks/libmmmojo.dylib", "Resources/libwxocr.dylib", "runtime.json"):
            if not (self.runtime / name).is_file():
                raise NativeError(f"Missing runtime file {name}; run scripts/prepare_runtime.py first")
        try:
            self.manifest = json.loads((self.runtime / "runtime.json").read_text())
            if not isinstance(self.manifest, dict):
                raise NativeError("Invalid runtime manifest")
            if type(self.manifest["format_version"]) is not int or self.manifest["format_version"] != 1:
                raise NativeError("Unsupported runtime manifest version")
        except (ValueError, KeyError, OSError) as exc:
            raise NativeError("Invalid runtime manifest") from exc

    def recognize(self, image: Path) -> dict:
        """Preserve the original result-only API and HTTP response contract."""
        return self.recognize_detailed(image).result

    def recognize_detailed(self, image: Path) -> RecognitionOutcome:
        """Recognize once and return monotonic timing data; see RecognitionOutcome."""
        started_ns = time.perf_counter_ns()
        image = Path(image).resolve(strict=True)
        if not image.is_file():
            raise ValueError("image must be a regular file")
        with tempfile.TemporaryDirectory(prefix="screen-ocr-worker-") as temp:
            directory = Path(temp)
            result_path = directory / "result.json"
            metrics_path = directory / "metrics.json"
            with (directory / "worker.log").open("wb") as log:
                command = [sys.executable, "-m", "screen_ocr.worker", "--runtime", str(self.runtime),
                           "--image", str(image), "--output", str(result_path), "--timeout", str(self.timeout),
                           "--metrics-output", str(metrics_path)]
                with self._lock:
                    if self._closed:
                        raise NativeError("OCR engine is closed")
                    worker = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
                    self._active.add(worker)
                try:
                    returncode = worker.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired as exc:
                    raise EngineTimeout(f"OCR exceeded {self.timeout:g} seconds") from exc
                finally:
                    # Also reap a native child left behind after a worker error.
                    self._terminate(worker)
                    with self._lock:
                        self._active.discard(worker)
            if returncode != 0:
                with (directory / "worker.log").open("rb") as log:
                    log.seek(0, os.SEEK_END)
                    log.seek(max(0, log.tell() - 4096))
                    detail = log.read().decode("utf-8", errors="replace").strip()
                raise NativeError(f"Native worker failed ({returncode}): {detail}")
            try:
                result = json.loads(result_path.read_text())
                metrics = _validate_worker_timings(json.loads(metrics_path.read_text()))
            except (OSError, ValueError) as exc:
                raise NativeError("Native worker did not produce a valid result and timing record") from exc
        metrics["wall_ms"] = (time.perf_counter_ns() - started_ns) / 1e6
        return RecognitionOutcome(result=result, timings=metrics)

    @staticmethod
    def _terminate(worker: subprocess.Popen):
        try:
            os.killpg(worker.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin killpg filters out zombies, then returns EPERM if the
            # group has no signalable member (xnu/bsd/kern/kern_sig.c:killpg1).
            # Confirm that only dead processes remain; never hide a genuine
            # permission failure while a live helper is still running.
            listing = subprocess.check_output(
                ["/bin/ps", "-A", "-o", "pgid=,stat="], text=True, timeout=5,
            )
            for row in listing.splitlines():
                group, state = row.split()
                if int(group) == worker.pid and not state.startswith("Z"):
                    raise
        worker.wait()

    def close(self):
        """Reject new requests and cancel/reap every active native process group."""
        with self._lock:
            self._closed = True
            active = list(self._active)
        for worker in active:
            self._terminate(worker)
