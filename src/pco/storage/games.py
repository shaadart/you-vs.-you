"""Repository for the raw and normalized game layers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from pco.importers.normalize import NORMALIZER_VERSION, NormalizedGame


class AccountConflict(ValueError):
    pass


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def start_run(conn: sqlite3.Connection, source: str, locator: str, complete: bool = True) -> int:
    cur = conn.execute(
        "INSERT INTO import_runs (source, locator, started_at, complete) VALUES (?, ?, ?, ?)",
        (source, locator, utcnow(), int(complete)),
    )
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection, run_id: int, counts: dict[str, int], status: str, error: str | None
) -> None:
    conn.execute(
        "UPDATE import_runs SET finished_at=?, status=?, error=?, n_seen=?, n_new=?,"
        " n_duplicate=?, n_rejected=? WHERE id=?",
        (
            utcnow(),
            status,
            error,
            counts["seen"],
            counts["new"],
            counts["duplicate"],
            counts["rejected"],
            run_id,
        ),
    )


def store_raw(conn: sqlite3.Connection, kind: str, text: str, run_id: int) -> int:
    """Insert a raw record unless identical content is already stored; return its id."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    row = conn.execute("SELECT id FROM raw_games WHERE content_sha256=?", (digest,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO raw_games (kind, content_sha256, raw_text, first_import_run_id, fetched_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (kind, digest, text, run_id, utcnow()),
    )
    return cur.lastrowid


def set_normalize_error(conn: sqlite3.Connection, raw_id: int, error: str | None) -> None:
    conn.execute("UPDATE raw_games SET normalize_error=? WHERE id=?", (error, raw_id))


def insert_game(conn: sqlite3.Connection, raw_id: int, g: NormalizedGame) -> bool:
    """Insert a normalized game; False if an equivalent game (same dedup key) exists."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO games (raw_game_id, dedup_key, platform, external_id, played_at,"
        " white, black, white_elo, black_elo, result, termination, tc_base_s, tc_inc_s,"
        " time_class, rated, start_fen, final_fen, moves_uci, n_plies, clocks_s,"
        " normalizer_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            raw_id,
            g.dedup_key,
            g.platform,
            g.external_id,
            g.played_at,
            g.white,
            g.black,
            g.white_elo,
            g.black_elo,
            g.result,
            g.termination,
            g.tc_base_s,
            g.tc_inc_s,
            g.time_class,
            None if g.rated is None else int(g.rated),
            g.start_fen,
            g.final_fen,
            " ".join(g.moves_uci),
            len(g.moves_uci),
            None if g.clocks_s is None else json.dumps(list(g.clocks_s)),
            NORMALIZER_VERSION,
        ),
    )
    return cur.rowcount == 1


def account_owner(conn: sqlite3.Connection, platform: str, username: str) -> str | None:
    row = conn.execute(
        "SELECT p.name FROM accounts a JOIN players p ON p.id = a.player_id"
        " WHERE a.platform=? AND a.username_lower=?",
        (platform, username.lower()),
    ).fetchone()
    return row[0] if row else None


def ensure_account(
    conn: sqlite3.Connection, player: str | None, platform: str, username: str
) -> int:
    """Link (platform, username) to a player and return the player id.

    With player=None an existing account keeps its owner, and a new account gets a
    player named after the username. Naming a different owner for a linked account
    raises AccountConflict.
    """
    row = conn.execute(
        "SELECT a.player_id, p.name FROM accounts a JOIN players p ON p.id = a.player_id"
        " WHERE a.platform=? AND a.username_lower=?",
        (platform, username.lower()),
    ).fetchone()
    if row is not None:
        if player is not None and player != row[1]:
            raise AccountConflict(f"{platform} account {username!r} already belongs to {row[1]!r}")
        return row[0]
    name = player or username
    conn.execute("INSERT OR IGNORE INTO players (name, created_at) VALUES (?, ?)", (name, utcnow()))
    player_id = conn.execute("SELECT id FROM players WHERE name=?", (name,)).fetchone()[0]
    conn.execute(
        "INSERT INTO accounts (player_id, platform, username, username_lower) VALUES (?, ?, ?, ?)",
        (player_id, platform, username, username.lower()),
    )
    return player_id


def last_successful_run(conn: sqlite3.Connection, source: str, locator: str) -> str | None:
    """Start time of the latest completed import from this source/locator (incremental base).

    Deliberately based on import runs, not stored games: games imported from a PGN file
    must not make an account import skip that account's older platform history.
    """
    row = conn.execute(
        "SELECT max(started_at) FROM import_runs"
        " WHERE source=? AND locator=? AND status='ok' AND complete=1",
        (source, locator),
    ).fetchone()
    return row[0]


def player_summary(conn: sqlite3.Connection) -> list[dict]:
    """Per player: game counts by platform/time class/color, decisions, and date range."""
    out = []
    for p in conn.execute("SELECT id, name FROM players ORDER BY name").fetchall():
        rows = conn.execute(
            "SELECT platform, coalesce(time_class, '?') AS tc, color, count(*) AS n,"
            # own moves: the side to move first gets the odd ply
            " sum((n_plies + ((instr(start_fen, ' w ') > 0) = (color = 'white'))) / 2) AS d,"
            " min(played_at) AS first, max(played_at) AS last"
            " FROM player_games WHERE player_id=? GROUP BY platform, tc, color",
            (p["id"],),
        ).fetchall()
        accounts = conn.execute(
            "SELECT platform, username FROM accounts WHERE player_id=? ORDER BY platform",
            (p["id"],),
        ).fetchall()
        out.append(
            {
                "name": p["name"],
                "accounts": [f"{a['platform']}:{a['username']}" for a in accounts],
                "games": sum(r["n"] for r in rows),
                "decisions": sum(r["d"] or 0 for r in rows),
                "first": min((r["first"] for r in rows if r["first"]), default=None),
                "last": max((r["last"] for r in rows if r["last"]), default=None),
                "breakdown": [dict(r) for r in rows],
            }
        )
    return out


def verify_games(conn: sqlite3.Connection) -> tuple[int, list[str]]:
    """Replay every stored game; returns (games checked, descriptions of failures)."""
    import chess  # local: only needed for verification

    failures, n = [], 0
    for row in conn.execute("SELECT id, start_fen, final_fen, moves_uci, n_plies FROM games"):
        n += 1
        board = chess.Board(row["start_fen"])
        moves = row["moves_uci"].split()
        try:
            for uci in moves:
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    raise ValueError(f"illegal move {uci}")
                board.push(move)
            if board.fen() != row["final_fen"] or len(moves) != row["n_plies"]:
                raise ValueError("final position or ply count mismatch")
        except ValueError as exc:
            failures.append(f"game {row['id']}: {exc}")
    return n, failures
