"""Open a link in the user's browser, safely from inside a frozen app."""

from __future__ import annotations

import logging
import os
import sys
import webbrowser

log = logging.getLogger("pco")


def open_url(url: str) -> bool:
    """webbrowser.open, with the app's own library path taken out of the browser's way.

    A PyInstaller app on Linux points LD_LIBRARY_PATH at its bundled libraries; every child
    process inherits it, so xdg-open or the browser would load the app's copies of system
    libraries and often die silently. PyInstaller keeps the user's original value in
    LD_LIBRARY_PATH_ORIG."""
    saved = os.environ.get("LD_LIBRARY_PATH")
    if getattr(sys, "frozen", False):
        original = os.environ.get("LD_LIBRARY_PATH_ORIG")
        if original is None:
            os.environ.pop("LD_LIBRARY_PATH", None)
        else:
            os.environ["LD_LIBRARY_PATH"] = original
    try:
        opened = webbrowser.open(url)
    except Exception as exc:
        log.warning("could not open a browser: %s: %s", type(exc).__name__, exc)
        return False
    finally:
        if saved is None:
            os.environ.pop("LD_LIBRARY_PATH", None)
        else:
            os.environ["LD_LIBRARY_PATH"] = saved
    if not opened:
        log.warning("no browser could be opened for %s", url.split("?")[0])
    return opened
