"""Import pipeline: raw records -> raw layer -> normalization -> deduplicated games.

Re-running any import is safe: identical raw content is stored once, and a game seen
again (same platform id, or same content when there is no id) is counted as a
duplicate. Progress is committed in batches so an interrupted import keeps its work.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field

from pco.importers.normalize import NormalizationError, normalize
from pco.importers.sources import RawRecord
from pco.storage import games as repo

COMMIT_EVERY = 200
MAX_REPORTED_ERRORS = 20


@dataclass
class ImportReport:
    run_id: int
    seen: int = 0
    new: int = 0
    duplicate: int = 0
    rejected: int = 0
    involving_player: int = 0  # valid games where the importing account played
    errors: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {k: getattr(self, k) for k in ("seen", "new", "duplicate", "rejected")}


def import_records(
    conn: sqlite3.Connection,
    source: str,
    locator: str,
    records: Iterable[RawRecord],
    *,
    username: str | None = None,
    player: str | None = None,
    complete: bool = True,
) -> ImportReport:
    """Store and normalize records. If `username` is given, games where that name plays
    link the (platform, username) account to `player` (default: the username).
    `complete=False` marks a deliberately partial fetch that later incremental
    imports must not treat as covering the account's history."""
    report = ImportReport(run_id=repo.start_run(conn, source, locator, complete))
    conn.commit()
    try:
        for record in records:
            report.seen += 1
            raw_id = repo.store_raw(conn, record.kind, record.text, report.run_id)
            try:
                game = normalize(record.kind, record.text)
            except NormalizationError as exc:
                repo.set_normalize_error(conn, raw_id, str(exc))
                report.rejected += 1
                if len(report.errors) < MAX_REPORTED_ERRORS:
                    report.errors.append(f"record {report.seen}: {exc}")
            else:
                repo.set_normalize_error(conn, raw_id, None)
                if repo.insert_game(conn, raw_id, game):
                    report.new += 1
                else:
                    report.duplicate += 1
                if username:
                    side = next(
                        (n for n in (game.white, game.black) if n.lower() == username.lower()),
                        None,
                    )
                    if side:
                        report.involving_player += 1
                        repo.ensure_account(conn, player, game.platform, side)
            if report.seen % COMMIT_EVERY == 0:
                conn.commit()
    except BaseException as exc:  # includes KeyboardInterrupt: keep committed progress
        conn.commit()
        repo.finish_run(conn, report.run_id, report.counts(), "failed", repr(exc))
        conn.commit()
        raise
    repo.finish_run(conn, report.run_id, report.counts(), "ok", None)
    conn.commit()
    return report
