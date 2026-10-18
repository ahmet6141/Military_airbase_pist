#!/usr/bin/env python3
"""Build the installable extension zip (no Blender needed).

    python3 tools/build_extension.py            -> dist/military_airbase_designer-<version>.zip

The zip contains the package folder with blender_manifest.toml at its root, which is
what Blender 4.2+ "Install from Disk" expects. Caches and tests are excluded.
"""
from __future__ import annotations

import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "military_airbase_designer"
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}
EXCLUDE_EXT = {".pyc", ".pyo", ".blend1"}


def version() -> str:
    text = open(os.path.join(ROOT, PKG, "blender_manifest.toml"), encoding="utf-8").read()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else "0.0.0"


def main() -> int:
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{PKG}-{version()}.zip")
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, PKG)):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in filenames:
                if os.path.splitext(fn)[1] in EXCLUDE_EXT:
                    continue
                full = os.path.join(dirpath, fn)
                arc = os.path.relpath(full, ROOT)
                zf.write(full, arc)
                count += 1
    print(f"wrote {out} ({count} files, {os.path.getsize(out) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
