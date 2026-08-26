#!/usr/bin/env python3
"""Assemble the Windows release zip.

Usage:
    python scripts/make_release.py [--version 0.3.0]

Layout inside the zip (the 'front page' a user sees):
    BoardAgent-v<version>/
        INSTALL.bat            <- double-click this
        uninstall.bat
        boardagent.exe
        boardagent-server.exe
        boardagent-mcp.exe
        README.txt

Builds the exes first (via build_exes.py), then zips everything into
dist/BoardAgent-v<version>.zip.
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def main() -> None:
    version = "0.1.0"
    if "--version" in sys.argv:
        version = sys.argv[sys.argv.index("--version") + 1]

    # 1. Build the exes if they are missing.
    needed = ["boardagent.exe", "boardagent-server.exe", "boardagent-mcp.exe"]
    missing = [n for n in needed if not (DIST / n).exists()]
    if missing:
        print("Building exes (missing):", ", ".join(missing))
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_exes.py")], check=True)

    # 2. Assemble the zip (flat layout, no dist/ nesting).
    zip_name = f"BoardAgent-v{version}.zip"
    zip_path = DIST / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in needed:
            zf.write(DIST / name, name)
        zf.write(ROOT / "INSTALL.bat", "INSTALL.bat")
        zf.write(ROOT / "uninstall.bat", "uninstall.bat")
        readme = ROOT / "README.txt"
        if readme.exists():
            zf.write(readme, "README.txt")
        else:
            # Tiny standalone README for the zip so users never see an
            # empty folder.
            zf.writestr(
                "README.txt",
                "BoardAgent\n"
                "=========\n\n"
                "1. Double-click INSTALL.bat\n"
                "2. Open the app:  boardagent.exe  (or type 'boardagent')\n\n"
                "Uninstall: double-click uninstall.bat\n",
            )
    print(f"Release zip: {zip_path}")


if __name__ == "__main__":
    main()
