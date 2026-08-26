"""PyInstaller entry point: boardagent-server.

--noconsole mode sets sys.stdout/sys.stderr to None. uvicorn's logging
configuration calls .isatty() on them and crashes with:
    AttributeError: 'NoneType' object has no attribute 'isatty'
Give the process no-op stream stand-ins so logging config works.
"""
import sys


class _NullStream:
    """A write-only /dev/null-ish stream for console-less processes."""

    encoding = "utf-8"
    errors = "replace"

    def write(self, *args, **kwargs) -> int:
        return 0

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("no fileno for null stream")

    def writelines(self, lines) -> None:
        return None


if sys.stdout is None:
    sys.stdout = _NullStream()
if sys.stderr is None:
    sys.stderr = _NullStream()

from boardagent.api import main

if __name__ == "__main__":
    main()
