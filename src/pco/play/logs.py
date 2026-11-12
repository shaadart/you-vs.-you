"""A small rotating log in the data directory: where errors go instead of the page."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(home: Path) -> Path:
    """Log to `home/logs/app.log` (3 files of 512 KB). Also to stderr when there is one."""
    path = home / "logs" / "app.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("pco")
    log.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in log.handlers):
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        file = RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
        file.setFormatter(fmt)
        log.addHandler(file)
        if sys.stderr is not None:  # a windowed, frozen app has none
            console = logging.StreamHandler(sys.stderr)
            console.setFormatter(fmt)
            log.addHandler(console)
    return path
