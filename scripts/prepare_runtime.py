"""Extract the verified macOS engine and compile its small ARM64 entry point.

Run with uv run scripts/prepare_runtime.py --download, --dmg FILE or --app APP.
Proprietary binaries stay in the ignored local runtime directory.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import tempfile
import urllib.request

DMG_URL = "https://dldir1v6.qq.com/weixin/Universal/Mac/WeChatMac_4.2.0.dmg"
DMG_SHA256 = "077b99658e2d2083458a181f6de21ac9442fef8479635e367afdb0e4b1be5f22"
LIBRARIES = {
    "Frameworks/libmmmojo.dylib": "1fd92398807aa17ccf4e6c0f9cf0a96e0202e3b7a49104169a188e4c515c6425",
    "Resources/libwxocr.dylib": "a0056c2a1cbf7c8017301d60e3bb35f07611073fe99a41ca6d62ea15657cc204",
}
MODELS = (
    "text_det_fp16_v2.xnet", "text_rec_zh13562_fp16_v2.xnet", "charset_zh13562.txt",
    "paragraph_det_fp16_v2.xnet", "QBarDetV1.7.0.26.xnet",
    "DeepSR_REPSR_R_M4C16_Flip_ep24.xnet", "QBarSegV1.1.0.26.xnet",
    "FPMMFG1V1.2.0.26.xnet", "FPMMFG2V1.3.1.26.xnet", "PrivacyDetectV8.0.26.xnet",
)


def digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


@contextmanager
def mounted_app(dmg: Path):
    if digest(dmg) != DMG_SHA256:
        raise ValueError("DMG checksum differs from the verified WeChat 4.2.0 package")
    with tempfile.TemporaryDirectory(prefix="wxocr-mount-") as temp:
        mount = Path(temp) / "volume"
        mount.mkdir()
        subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(dmg)], check=True)
        try:
            yield mount / "WeChat.app"
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True)


def prepare(app: Path, output: Path):
    if output.exists():
        raise ValueError(f"Runtime already exists: {output}; choose a new --output directory")
    contents = app / "Contents"
    with (contents / "Info.plist").open("rb") as file:
        info = plistlib.load(file)
    if (info["CFBundleShortVersionString"], info["CFBundleVersion"]) != ("4.2.0", "270352"):
        raise ValueError("Only the verified WeChat 4.2.0 build 270352 is supported")
    for name, expected in LIBRARIES.items():
        if digest(contents / name) != expected:
            raise ValueError(f"Unrecognized engine binary: {name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".wxocr-stage-", dir=output.parent) as temp:
        stage = Path(temp) / "runtime"
        for folder in ("bin", "Frameworks", "Resources"):
            (stage / folder).mkdir(parents=True)
        files = [*LIBRARIES, *(f"Resources/{name}" for name in MODELS)]
        for name in files:
            shutil.copy2(contents / name, stage / name)
        helper = Path(__file__).resolve().parents[1] / "native/ocr_helper.c"
        subprocess.run(["xcrun", "clang", "-arch", "arm64", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(helper), "-Wl,-rpath,@loader_path/../Frameworks", "-o", str(stage / "bin/wxocr-engine")], check=True)
        manifest = {
            "format_version": 1,
            "engine": "WeChat macOS OCR",
            "source_version": "4.2.0",
            "source_build": "270352",
            "architecture": "arm64",
            "source_url": DMG_URL,
            "source_dmg_sha256": DMG_SHA256,
            "sha256": {name: digest(stage / name) for name in [*files, "bin/wxocr-engine"]},
        }
        (stage / "runtime.json").write_text(json.dumps(manifest, indent=2) + "\n")
        stage.rename(output)
    print(f"Prepared native runtime: {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--app", type=Path, help="Path to the verified WeChat.app (it will not be modified)")
    source.add_argument("--dmg", type=Path, help="Existing official WeChatMac_4.2.0.dmg")
    source.add_argument("--download", action="store_true", help="Download the pinned official 519 MiB package")
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
    else:
        with tempfile.TemporaryDirectory(prefix="wxocr-download-") as temp:
            dmg = Path(temp) / "WeChatMac_4.2.0.dmg"
            print(f"Downloading {DMG_URL}", flush=True)
            with urllib.request.urlopen(DMG_URL, timeout=60) as response, dmg.open("wb") as target:
                shutil.copyfileobj(response, target)
            with mounted_app(dmg) as app:
                prepare(app, output)


if __name__ == "__main__":
    main()
