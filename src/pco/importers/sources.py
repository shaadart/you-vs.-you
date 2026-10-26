"""Source adapters: each yields RawRecords exactly as received, without interpreting them.

External platform details (URLs, pagination, auth) live here only; everything
downstream sees (kind, text) pairs handled by `normalize.normalize`.
"""

from __future__ import annotations

import io
import json
import re
import urllib.parse
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import chess.pgn

from pco.importers.http import Http, HttpError

LICHESS_API = "https://lichess.org"
CHESSCOM_API = "https://api.chess.com/pub"
# Standard-chess speeds only; variants are filtered server-side to save bandwidth.
_LICHESS_PERFS = "ultraBullet,bullet,blitz,rapid,classical,correspondence"


@dataclass(frozen=True)
class RawRecord:
    kind: str  # key into normalize.NORMALIZERS
    text: str


class SourceError(RuntimeError):
    pass


def pgn_records(path: Path) -> Iterator[RawRecord]:
    """Split a multi-game PGN file into per-game text slices, byte-for-byte from the file."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")  # old desktop exports
    except OSError as exc:
        raise SourceError(f"cannot read {path}: {exc}") from exc
    yield from pgn_text_records(text)


def pgn_text_records(text: str) -> Iterator[RawRecord]:
    """Split multi-game PGN text (e.g. an uploaded file) into per-game raw records."""
    handle = io.StringIO(text)
    starts = []
    while True:
        offset = handle.tell()
        if chess.pgn.read_headers(handle) is None:  # consumes headers *and* movetext
            break
        starts.append(offset)
    starts.append(len(text))
    for begin, end in zip(starts, starts[1:], strict=False):
        chunk = text[begin:end].strip()
        if chunk:
            yield RawRecord("pgn", chunk)


def lichess_game_count(http: Http, username: str) -> int | None:
    """The account's total number of games (all variants), or None when unavailable.
    Only used to tell how far along an import is."""
    try:
        data = http.get_json(f"{LICHESS_API}/api/user/{urllib.parse.quote(username)}")
        return int(data["count"]["all"])
    except (HttpError, KeyError, TypeError, ValueError):
        return None


def lichess_records(
    http: Http,
    username: str,
    *,
    since_ms: int | None = None,
    max_games: int | None = None,
    token: str | None = None,
) -> Iterator[RawRecord]:
    """Stream a user's games as NDJSON (one record per line, full PGN embedded)."""
    params = {
        "clocks": "true",
        "pgnInJson": "true",
        "opening": "false",
        "perfType": _LICHESS_PERFS,
    }
    if since_ms is not None:
        params["since"] = str(since_ms)
    if max_games is not None:
        params["max"] = str(max_games)
    url = f"{LICHESS_API}/api/games/user/{urllib.parse.quote(username)}?" + urllib.parse.urlencode(
        params
    )
    headers = {"Accept": "application/x-ndjson"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        # Lichess ignores `max` when combined with perfType (observed 2026-10-23), so the
        # limit is also enforced here; closing the generator closes the stream.
        lines = http.get_lines(url, headers)
        for n, line in enumerate(lines, start=1):
            yield RawRecord("lichess-ndjson", line)
            if max_games is not None and n >= max_games:
                lines.close()
                break
    except HttpError as exc:
        if exc.status == 404:
            raise SourceError(f"lichess user {username!r} not found (or export refused)") from exc
        raise SourceError(str(exc)) from exc


_ARCHIVE_MONTH = re.compile(r"/games/(\d{4})/(\d{2})$")


def chesscom_records(
    http: Http,
    username: str,
    *,
    since_month: str | None = None,
    on_month: Callable[[int, int], None] | None = None,
) -> Iterator[RawRecord]:
    """Walk monthly archives oldest first; skip months before `since_month` ('YYYY-MM').
    `on_month(done, total)` is called as each month to read is started."""
    user = urllib.parse.quote(username.lower())
    try:
        archives = http.get_json(f"{CHESSCOM_API}/player/{user}/games/archives")["archives"]
    except HttpError as exc:
        if exc.status == 404:
            raise SourceError(f"chess.com user {username!r} not found") from exc
        raise SourceError(str(exc)) from exc

    def wanted(url: str) -> bool:
        m = _ARCHIVE_MONTH.search(url)
        return not (m and since_month and f"{m.group(1)}-{m.group(2)}" < since_month)

    months = [url for url in archives if wanted(url)]
    for done, url in enumerate(months):
        if on_month:
            on_month(done, len(months))
        try:
            games = http.get_json(url).get("games", [])
        except HttpError as exc:
            raise SourceError(str(exc)) from exc
        for game in games:
            # Canonical re-serialization of one record: lossless, stable for hashing.
            yield RawRecord("chesscom-json", json.dumps(game, sort_keys=True, ensure_ascii=False))
