#!/usr/bin/env python3
"""Build the BoardAgent Windows exes with PyInstaller.

Usage:
    python scripts/build_exes.py

Output lands in dist/:
    boardagent-server.exe   background service (no console window)
    boardagent.exe          terminal UI (Textual, console app)
    boardagent-mcp.exe      MCP stdio server (for MCP hosts)

Notes:
- --collect-submodules textual is REQUIRED: Textual lazy-imports its widget
  submodules (textual.widgets._tab_pane etc.), which PyInstaller's static
  analysis misses without it.
- --collect-data boardagent bundles the built-in theme JSON files.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "packaging"
DIST = ROOT / "dist"


def build(name: str, script: str, console: bool) -> None:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", name,
        "--collect-data", "boardagent",
        "--collect-submodules", "textual",
    ]
    if not console:
        cmd.insert(3, "--noconsole")  # after --onefile, before --name
    cmd.append(str(ENTRY / script))
    print(f"== building {name} ==")
    subprocess.run(cmd, cwd=ROOT, check=True)
    print(f"== {name} done: {DIST / (name + '.exe')} ==")


def main() -> None:
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    build("boardagent-server", "entry_server.py", console=False)
    build("boardagent", "entry_tui.py", console=True)
    build("boardagent-mcp", "entry_mcp.py", console=True)
    print("\nAll exes built in dist/.")


if __name__ == "__main__":
    main()
