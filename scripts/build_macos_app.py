"""Build a small, locally signed menu bar app; optionally install and open it.

The app uses the prepared Python environment and runtime in this checkout.
It contains neither proprietary runtime assets nor a Python distribution.
"""

import argparse
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "apps/macos"
BUNDLE_ID = "dev.screenocr.menubar"
APP_NAME = "Screen OCR.app"


def ensure_replaceable(destination: Path) -> None:
    if destination.is_symlink():
        raise ValueError(f"Refusing to replace a symlink: {destination}")
    if not destination.exists():
        return
    with (destination / "Contents/Info.plist").open("rb") as file:
        info = plistlib.load(file)
    if info["CFBundleIdentifier"] != BUNDLE_ID:
        raise ValueError(f"Another application already occupies {destination}")
    executable = destination / "Contents/MacOS/ScreenOCR"
    running = subprocess.run(
        ["/usr/bin/pgrep", "-f", "^" + re.escape(str(executable)) + r"([[:space:]]|$)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    if running.returncode == 0:
        raise ValueError(f"Quit Screen OCR before replacing {destination}")
    if running.returncode != 1:
        raise ValueError(f"Could not check whether the app is running: {running.stderr.strip()}")


def replace_bundle(source: Path, destination: Path) -> None:
    ensure_replaceable(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".screen-ocr-install-", dir=destination.parent) as folder:
        stage = Path(folder) / APP_NAME
        shutil.copytree(source, stage)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(stage)], check=True)
        previous = Path(folder) / "previous.app"
        if destination.exists():
            destination.rename(previous)
        try:
            stage.rename(destination)
        except BaseException:
            if previous.exists():
                previous.rename(destination)
            raise


def build() -> Path:
    output = ROOT / "dist" / APP_NAME
    ensure_replaceable(output)
    output.parent.mkdir(exist_ok=True)
    command = ["xcrun", "swift", "build", "--package-path", str(PACKAGE),
               "--configuration", "release", "--arch", "arm64"]
    subprocess.run(command, check=True)
    binaries = Path(subprocess.check_output([*command, "--show-bin-path"], text=True).strip())
    with tempfile.TemporaryDirectory(prefix=".screen-ocr-build-", dir=output.parent) as folder:
        stage = Path(folder) / APP_NAME
        contents = stage / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Resources").mkdir()
        shutil.copy2(binaries / "ScreenOCR", contents / "MacOS/ScreenOCR")
        shutil.copy2(PACKAGE / "Packaging/Info.plist", contents / "Info.plist")
        iconset = Path(folder) / "AppIcon.iconset"
        subprocess.run(["xcrun", "swift", str(PACKAGE / "Packaging/MakeIcon.swift"), str(iconset)], check=True)
        subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(iconset),
                        "-o", str(contents / "Resources/AppIcon.icns")], check=True)
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none",
                        "--identifier", BUNDLE_ID, str(stage)], check=True)
        replace_bundle(stage, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Install into ~/Applications/Screen OCR.app")
    parser.add_argument("--open", action="store_true", help="Open the app configured for this checkout")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("Requires macOS on Apple Silicon with Swift 6 or newer")
    try:
        app = build()
        if args.install:
            destination = Path.home() / "Applications" / APP_NAME
            replace_bundle(app, destination)
            app = destination
        print(f"Built Screen OCR: {app}")
        if args.open:
            subprocess.run(["/usr/bin/open", str(app), "--args", "--project-directory", str(ROOT)], check=True)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"screen-ocr app build: {error}\n")


if __name__ == "__main__":
    main()
