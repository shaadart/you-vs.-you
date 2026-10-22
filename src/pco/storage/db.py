"""SQLite connection and ordered schema migrations.

Layers: raw (import_runs, raw_games) -> normalized (games, players, accounts).
Raw rows are never modified except for their `normalize_error` diagnostic.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pco.storage.migrations import MIGRATIONS


class SchemaError(RuntimeError):
    pass


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) and migrate the database. ':memory:' is allowed."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT max(version) FROM schema_version").fetchone()
    return row[0] or 0


def migrate(conn: sqlite3.Connection) -> int:
    current = schema_version(conn)
    latest = len(MIGRATIONS)
    if current > latest:
        raise SchemaError(
            f"database schema v{current} is newer than this pco supports (v{latest}); upgrade pco"
        )
    conn.commit()
    for version in range(current + 1, latest + 1):
        # Schema change and version bump commit atomically, or not at all.
        conn.executescript(
            f"BEGIN; {MIGRATIONS[version - 1]};\n"
            f"INSERT INTO schema_version (version) VALUES ({version}); COMMIT;"
        )
    return latest
