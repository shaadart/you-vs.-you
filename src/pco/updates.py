"""Is a newer release out? One anonymous GET to GitHub's public API, at most once per launch.

The only network call the app makes that the user did not ask for. It sends no identifiers
beyond the client name, can be turned off in Settings, and never blocks the app.
"""

from __future__ import annotations

import re
from typing import Any

from pco import __version__
from pco.importers.http import Http, HttpError

RELEASES_API = "https://api.github.com/repos/shaadart/you-vs.-you/releases/latest"
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(tag: str) -> tuple[int, int, int] | None:
    m = _VERSION.match(tag.strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def newer_release(http: Http, current: str = __version__) -> dict[str, Any] | None:
    """{"version", "url"} when the latest release is newer than `current`, else None."""
    try:
        data = http.get_json(RELEASES_API, {"Accept": "application/vnd.github+json"})
    except (HttpError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    latest, have = parse_version(str(data.get("tag_name", ""))), parse_version(current)
    url = data.get("html_url")
    if latest is None or have is None or latest <= have or not isinstance(url, str):
        return None
    if not url.startswith("https://github.com/shaadart/you-vs.-you/"):
        return None
    return {"version": ".".join(map(str, latest)), "url": url}
