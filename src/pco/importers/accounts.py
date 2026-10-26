"""Account imports shared by the CLI and the interface (one code path, same guarantees)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from pco.config import Settings
from pco.importers.http import Http
from pco.importers.pipeline import ImportReport, import_records
from pco.importers.sources import chesscom_records, lichess_records
from pco.storage import games as repo

# Lichess `since` filters on game creation, so games still running during the previous
# import started before it; a margin re-fetches them (duplicates are free). Correspondence
# games longer than the margin need a full import.
LICHESS_SINCE_MARGIN_MS = 24 * 3600 * 1000


def lichess_since_ms(last_run: str | None) -> int | None:
    if not last_run:
        return None
    t = datetime.strptime(last_run, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return int(t.timestamp() * 1000) - LICHESS_SINCE_MARGIN_MS


def import_account(
    conn: sqlite3.Connection,
    http: Http,
    settings: Settings,
    platform: str,
    username: str,
    player: str | None = None,
    full: bool = False,
    max_games: int | None = None,
    progress: Callable[[ImportReport], None] | None = None,
) -> tuple[ImportReport, str | None]:
    """Import a Chess.com/Lichess account incrementally. Returns (report, incremental-since)."""
    locator = username.lower()
    # Fail fast on an ownership conflict; the account itself is linked by the pipeline only
    # once games actually arrive (no dangling accounts on errors).
    owner = repo.account_owner(conn, platform, username)
    if player and owner and owner != player:
        raise repo.AccountConflict(f"{platform} account {username!r} belongs to {owner!r}")
    last = None if full else repo.last_successful_run(conn, platform, locator)
    if platform == "lichess":
        records = lichess_records(
            http,
            username,
            since_ms=lichess_since_ms(last),
            max_games=max_games,
            token=settings.lichess_token,
        )
    elif platform == "chesscom":  # archives are monthly: re-read the month of the last import
        records = chesscom_records(http, username, since_month=last and last[:7])
    else:
        raise ValueError(f"unknown platform {platform!r}")
    report = import_records(
        conn,
        platform,
        locator,
        records,
        username=username,
        player=player,
        complete=max_games is None,
        progress=progress,
    )
    return report, last
