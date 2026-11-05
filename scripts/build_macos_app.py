"""Legacy build entry point. New builds can run the Swift script directly.

The installed App and the Swift packager do not use Python or a virtualenv.
"""

from pathlib import Path
import subprocess
import sys


if __name__ == "__main__":
    script = Path(__file__).resolve().with_suffix(".swift")
    raise SystemExit(subprocess.call(["/usr/bin/xcrun", "swift", str(script), *sys.argv[1:]]))
