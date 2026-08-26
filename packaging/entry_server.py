"""PyInstaller entry point: boardagent-server.

--noconsole mode sets sys.stdout/sys.stderr to None. uvicorn's logging
configuration calls .isatty() (and possibly fileno()) on them and crashes
with AttributeError. Point them at os.devnull instead — a real file object
implements the full stream protocol.
"""
import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from boardagent.api import main

if __name__ == "__main__":
    main()
