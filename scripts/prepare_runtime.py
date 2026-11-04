"""Extract the verified macOS engine and compile its small ARM64 entry point.

Run with uv run scripts/prepare_runtime.py --download, --dmg FILE, --app APP
or --runtime DIR to reuse already verified local assets.
Proprietary binaries stay in the ignored local runtime directory.
"""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import tempfile
import urllib.request

from screen_ocr.runtime_assets import load_baseline, sha256_file, verify_assets

BASELINE = load_baseline()
DMG_URL = BASELINE["source_url"]
DMG_SHA256 = BASELINE["source_dmg_sha256"]
SOURCE_LABEL = f"{BASELINE['source_version']} build {BASELINE['source_build']}"


@contextmanager
def mounted_app(dmg: Path):
    if sha256_file(dmg) != DMG_SHA256:
        raise ValueError(f"DMG checksum differs from the verified {SOURCE_LABEL} runtime package")
    with tempfile.TemporaryDirectory(prefix="screen-ocr-mount-") as temp:
        mount = Path(temp) / "volume"
        mount.mkdir()
        subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(dmg)], check=True)
        try:
            yield mount / "WeChat.app"
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True)


def prepare(app: Path, output: Path):
    contents = app / "Contents"
    with (contents / "Info.plist").open("rb") as file:
        info = plistlib.load(file)
    if (info["CFBundleShortVersionString"], info["CFBundleVersion"]) != (BASELINE["source_version"], BASELINE["source_build"]):
        raise ValueError(f"Only the verified runtime {SOURCE_LABEL} is supported")
    prepare_contents(contents, output)


def prepare_contents(contents: Path, output: Path):
    """Copy pinned source assets and compile our helper; old manifests are not trusted."""
    if output.exists():
        raise ValueError(f"Runtime already exists: {output}; choose a new --output directory")
    verify_assets(contents, BASELINE)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".screen-ocr-stage-", dir=output.parent) as temp:
        stage = Path(temp) / "runtime"
        for folder in ("bin", "Frameworks", "Resources"):
            (stage / folder).mkdir(parents=True)
        files = [entry["path"] for entry in BASELINE["files"]]
        for name in files:
            shutil.copy2(contents / name, stage / name)
        # Re-check the staged copy in case an installed source application was
        # updated while files were being copied.
        verify_assets(stage, BASELINE)
        helper = Path(__file__).resolve().parents[1] / "native/ocr_helper.c"
        subprocess.run(["xcrun", "clang", "-arch", "arm64", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(helper), "-Wl,-rpath,@loader_path/../Frameworks", "-o", str(stage / "bin/screen-ocr-engine")], check=True)
        manifest = {
            "format_version": 1,
            "engine": "Third-party native macOS OCR",
            "source_version": BASELINE["source_version"],
            "source_build": BASELINE["source_build"],
            "architecture": BASELINE["architecture"],
            "source_url": DMG_URL,
            "source_dmg_sha256": DMG_SHA256,
            "sha256": {name: sha256_file(stage / name) for name in [*files, "bin/screen-ocr-engine"]},
        }
        (stage / "runtime.json").write_text(json.dumps(manifest, indent=2) + "\n")
        stage.rename(output)
    print(f"Prepared native runtime: {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--app", type=Path, help="Path to the verified source application (it will not be modified)")
    source.add_argument("--dmg", type=Path, help=f"Existing official {SOURCE_LABEL} runtime DMG")
    source.add_argument("--download", action="store_true", help="Download the pinned official runtime package")
    source.add_argument("--runtime", type=Path, help="Reuse local runtime assets after checking every pinned file")
    parser.add_argument("--output", type=Path, default=Path(".runtime"))
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("Requires macOS on Apple Silicon and Xcode Command Line Tools")
    output = args.output.resolve()
    if output.exists():
        parser.error(f"Runtime already exists: {output}; choose a new --output directory")
    if args.app:
        prepare(args.app.resolve(strict=True), output)
    elif args.dmg:
        with mounted_app(args.dmg.resolve(strict=True)) as app:
            prepare(app, output)
    elif args.runtime:
        prepare_contents(args.runtime.resolve(strict=True), output)
    else:
        with tempfile.TemporaryDirectory(prefix="screen-ocr-download-") as temp:
            dmg = Path(temp) / "source-runtime.dmg"
            print(f"Downloading {DMG_URL}", flush=True)
            with urllib.request.urlopen(DMG_URL, timeout=60) as response, dmg.open("wb") as target:
                shutil.copyfileobj(response, target)
            with mounted_app(dmg) as app:
                prepare(app, output)


if __name__ == "__main__":
    main()
