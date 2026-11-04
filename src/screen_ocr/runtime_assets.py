"""Read-only byte inventories for the pinned third-party runtime assets.

Matching hashes establish byte identity, not a license, engine compatibility,
model quality or a requirement to update. No inspected library is loaded.
"""

import argparse
from contextlib import contextmanager
import hashlib
from importlib.resources import files
import json
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import subprocess
import sys
import tempfile


_BASELINE_FIELDS = {
    "format_version", "source_version", "source_build", "architecture",
    "source_url", "source_dmg_sha256", "files",
}
_FILE_FIELDS = {"path", "category", "bytes", "sha256"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def sha256_file(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def validate_baseline(value: object) -> dict:
    """Accept exactly the version-1 inventory contract; do not infer aliases."""
    if not isinstance(value, dict) or value.keys() != _BASELINE_FIELDS:
        raise ValueError("Invalid baseline fields")
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        raise ValueError("Unsupported baseline format_version")
    for name in ("source_version", "source_build", "source_url"):
        if not isinstance(value[name], str) or not value[name]:
            raise ValueError(f"Invalid baseline {name}")
    if value["architecture"] != "arm64":
        raise ValueError("Baseline architecture must be arm64")
    if not isinstance(value["source_dmg_sha256"], str) or not _SHA256.fullmatch(value["source_dmg_sha256"]):
        raise ValueError("Invalid baseline source_dmg_sha256")
    if not isinstance(value["files"], list) or not value["files"]:
        raise ValueError("Baseline files must be a nonempty list")
    seen = set()
    for item in value["files"]:
        if not isinstance(item, dict) or item.keys() != _FILE_FIELDS:
            raise ValueError("Invalid baseline file fields")
        name = item["path"]
        if not isinstance(name, str) or "\0" in name or "\\" in name:
            raise ValueError("Invalid baseline asset path")
        path = PurePosixPath(name)
        if (len(path.parts) != 2 or path.parts[0] not in ("Frameworks", "Resources")
                or path.parts[1] in (".", "..") or path.as_posix() != name or name in seen):
            raise ValueError(f"Invalid or duplicate baseline asset path: {name}")
        seen.add(name)
        if item["category"] not in ("library", "model", "charset"):
            raise ValueError(f"Invalid asset category: {name}")
        if type(item["bytes"]) is not int or item["bytes"] < 0:
            raise ValueError(f"Invalid asset byte count: {name}")
        if not isinstance(item["sha256"], str) or not _SHA256.fullmatch(item["sha256"]):
            raise ValueError(f"Invalid asset SHA-256: {name}")
    return value


def load_baseline(path: Path | None = None) -> dict:
    if path is None:
        raw = files("screen_ocr").joinpath("data/runtime-baseline.json").read_text(encoding="utf-8")
    else:
        raw = path.read_text(encoding="utf-8")
    return validate_baseline(json.loads(raw))


def _asset_path(root: Path, name: str) -> Path:
    path = root / name
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"Asset symlink escapes the inspected directory: {name}")
    return path


def compare_assets(root: Path, baseline: dict) -> dict:
    """Compare known files and inventory additional top-level .xnet resources."""
    validate_baseline(baseline)
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Asset root must be a directory")
    records = []
    summary = {"unchanged": 0, "changed": 0, "missing": 0}
    for item in baseline["files"]:
        path = _asset_path(root, item["path"])
        if not path.exists():
            size, digest, status = None, None, "missing"
        elif not path.is_file():
            raise ValueError(f"Expected a regular asset file: {item['path']}")
        else:
            size, digest = path.stat().st_size, sha256_file(path)
            status = "unchanged" if (size, digest) == (item["bytes"], item["sha256"]) else "changed"
        summary[status] += 1
        records.append({
            "path": item["path"], "category": item["category"], "status": status,
            "expected_bytes": item["bytes"], "expected_sha256": item["sha256"],
            "actual_bytes": size, "actual_sha256": digest,
        })
    known = {item["path"] for item in baseline["files"]}
    additional = []
    for path in sorted((root / "Resources").glob("*.xnet")):
        name = path.relative_to(root).as_posix()
        path = _asset_path(root, name)
        if name not in known and path.is_file():
            additional.append({"path": name, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return {
        "files": records, "summary": summary, "additional_xnet_files": additional,
        "all_expected_files_match": summary["changed"] == summary["missing"] == 0,
        "compatibility_verified": False,
    }


def verify_assets(root: Path, baseline: dict) -> None:
    """Require every pinned asset to match before preparing a runtime."""
    result = compare_assets(root, baseline)
    changed = [f"{item['path']} ({item['status']})" for item in result["files"] if item["status"] != "unchanged"]
    if changed:
        raise ValueError("Unrecognized runtime assets: " + ", ".join(changed))


def app_version(app: Path) -> dict:
    with (app / "Contents/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    result = {"source_version": info["CFBundleShortVersionString"], "source_build": info["CFBundleVersion"]}
    if any(not isinstance(value, str) or not value for value in result.values()):
        raise ValueError("Source application version/build must be nonempty strings")
    return result


def runtime_version(runtime: Path) -> dict:
    manifest = runtime / "runtime.json"
    if not manifest.exists():
        return {"source_version": None, "source_build": None}
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or type(value["format_version"]) is not int or value["format_version"] != 1:
        raise ValueError("Unsupported runtime manifest")
    result = {"source_version": value["source_version"], "source_build": value["source_build"]}
    if any(not isinstance(item, str) or not item for item in result.values()):
        raise ValueError("Invalid runtime source version/build")
    return result


@contextmanager
def mounted_candidate(dmg: Path):
    """Mount any candidate read-only; unlike installation, do not enforce pins."""
    if platform.system() != "Darwin":
        raise ValueError("Inspecting a DMG requires macOS; use --runtime for extracted files")
    with tempfile.TemporaryDirectory(prefix="screen-ocr-inspect-") as folder:
        mount = Path(folder) / "volume"
        mount.mkdir()
        subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(dmg)],
                       check=True, capture_output=True, text=True)
        try:
            app = mount / "WeChat.app"
            if not app.is_dir():
                raise ValueError("Candidate DMG has a different application layout; mount it and use --app with its exact path")
            yield app
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True, capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--runtime", type=Path, help="Extracted runtime directory; runtime.json is optional")
    source.add_argument("--app", type=Path, help="Exact source application path, including a newer version")
    source.add_argument("--dmg", type=Path, help="Read-only inspection of an existing candidate DMG")
    parser.add_argument("--baseline", type=Path, help="Version-1 baseline JSON; default is the packaged verified baseline")
    args = parser.parse_args(argv)
    try:
        baseline = load_baseline(args.baseline)
        if args.runtime is not None:
            runtime = args.runtime.expanduser().resolve(strict=True)
            candidate = {"kind": "runtime", **runtime_version(runtime), "dmg_sha256": None}
            result = compare_assets(runtime, baseline)
        elif args.app is not None:
            app = args.app.expanduser().resolve(strict=True)
            candidate = {"kind": "app", **app_version(app), "dmg_sha256": None}
            result = compare_assets(app / "Contents", baseline)
        else:
            dmg = args.dmg.expanduser().resolve(strict=True)
            dmg_hash = sha256_file(dmg)
            with mounted_candidate(dmg) as app:
                candidate = {"kind": "dmg", **app_version(app), "dmg_sha256": dmg_hash}
                result = compare_assets(app / "Contents", baseline)
        report = {
            "format_version": 1, "comparison": "file_bytes_only",
            "baseline": {key: baseline[key] for key in ("source_version", "source_build", "architecture", "source_dmg_sha256")},
            "candidate": candidate, **result,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if result["all_expected_files_match"] else 1
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"screen-ocr inspect: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
