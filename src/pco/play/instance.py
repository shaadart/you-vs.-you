"""One app at a time per data directory.

Two processes on the same home would fight over the analysis cache's write lock and could
corrupt a half-written profile, so the app takes an exclusive OS lock on a file in the home.
The lock dies with the process, so a crash never leaves a stale lock behind.
"""

from __future__ import annotations

import sys
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = self.path.open("a+")
        try:
            if sys.platform == "win32":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise AlreadyRunning("You vs. You is already open.") from None
        self._file = f

    def release(self) -> None:
        f, self._file = self._file, None
        if f is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            f.close()  # closing drops a POSIX flock
