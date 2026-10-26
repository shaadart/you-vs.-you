"""Analysis cache: prior distributions and engine evaluations, in a separate SQLite file.

Lives under `Settings.cache_dir`, so deleting it never touches game data: everything
in it is reproducible from games + pinned models. Keys hash the producer identity
(model id / engine id), its settings and its exact inputs, so a model or budget change
misses instead of silently mixing results.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import threading
import zlib
from datetime import UTC, datetime
from pathlib import Path

from pco.analysis.stockfish import CandidateEval

CACHE_SCHEMA = 1
# Prior cache keys embed the prior's input_key format; a new format orphans old rows, so
# only the (cheap to recompute) prior table is cleared, never the engine table.
PRIOR_KEY_FORMAT = "k2"


def canonical_logprobs(logprobs: dict[str, float]) -> dict[str, float]:
    """The stored precision. Fresh and cached results both go through this, so a first
    build and a cached rebuild produce identical derived data."""
    return {m: round(lp, 6) for m, lp in logprobs.items()}


def cache_key(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()


class AnalysisCache:
    def __init__(self, path: Path | str, busy_timeout: float = 30.0, best_effort: bool = False):
        """`best_effort=True` (live play): when another process holds the file's write lock,
        skip storing instead of waiting `busy_timeout` and failing - it is only a cache."""
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.busy_timeout, self.best_effort = busy_timeout, best_effort
        self.hits = {"prior": 0, "engine": 0}
        self.misses = {"prior": 0, "engine": 0}
        self.skipped_writes = 0
        try:
            self._open(path)
        except sqlite3.DatabaseError as exc:
            if str(path) == ":memory:" or "locked" in str(exc):  # busy is not damaged
                raise
            # Derived, reproducible data: set the damaged file aside and start fresh.
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            moved = Path(f"{path}.corrupt-{stamp}")
            Path(path).replace(moved)
            print(
                f"warning: analysis cache {path} was unreadable ({exc}); moved to {moved}"
                " and starting a new cache (analysis will be recomputed; no games are lost)",
                file=sys.stderr,
            )
            self._open(path)

    def _open(self, path: Path | str) -> None:
        # Shared by engine worker threads; all access goes through self.lock.
        self.conn = sqlite3.connect(path, timeout=self.busy_timeout, check_same_thread=False)
        with self.lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS prior_cache (
                    key TEXT PRIMARY KEY, model_id TEXT NOT NULL, logprobs BLOB NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS engine_cache (
                    key TEXT PRIMARY KEY, engine_id TEXT NOT NULL, nodes INTEGER NOT NULL,
                    evals TEXT NOT NULL, created_at TEXT NOT NULL);
                """
            )
            row = self.conn.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
            if row is None:
                self.conn.execute("INSERT INTO meta VALUES ('schema', ?)", (str(CACHE_SCHEMA),))
            elif int(row[0]) != CACHE_SCHEMA:  # derived data: safe to rebuild
                self.conn.executescript("DELETE FROM prior_cache; DELETE FROM engine_cache;")
                self.conn.execute("UPDATE meta SET v=? WHERE k='schema'", (str(CACHE_SCHEMA),))
            fmt = self.conn.execute("SELECT v FROM meta WHERE k='prior_key'").fetchone()
            if fmt is None or fmt[0] != PRIOR_KEY_FORMAT:
                self.conn.execute("DELETE FROM prior_cache")
                self.conn.execute(
                    "INSERT OR REPLACE INTO meta VALUES ('prior_key', ?)", (PRIOR_KEY_FORMAT,)
                )
            self.conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def get_prior(self, key: str) -> dict[str, float] | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT logprobs FROM prior_cache WHERE key=?", (key,)
            ).fetchone()
        self._count("prior", row)
        return None if row is None else json.loads(zlib.decompress(row[0]))

    def _write(self, sql: str, params: tuple) -> None:
        with self.lock:
            try:
                self.conn.execute(sql, params)
            except sqlite3.OperationalError:  # "database is locked" by another process
                if not self.best_effort:
                    raise
                self.skipped_writes += 1

    def put_prior(self, key: str, model_id: str, logprobs: dict[str, float]) -> None:
        blob = zlib.compress(json.dumps(canonical_logprobs(logprobs)).encode())
        self._write(
            "INSERT OR REPLACE INTO prior_cache VALUES (?, ?, ?, ?)",
            (key, model_id, blob, self._now()),
        )

    def get_engine(self, key: str) -> list[CandidateEval] | None:
        with self.lock:
            row = self.conn.execute("SELECT evals FROM engine_cache WHERE key=?", (key,)).fetchone()
        self._count("engine", row)
        return None if row is None else [CandidateEval(*e) for e in json.loads(row[0])]

    def put_engine(self, key: str, engine_id: str, nodes: int, evals: list[CandidateEval]) -> None:
        payload = json.dumps([[e.uci, e.cp, e.mate, e.depth] for e in evals])
        self._write(
            "INSERT OR REPLACE INTO engine_cache VALUES (?, ?, ?, ?, ?)",
            (key, engine_id, nodes, payload, self._now()),
        )

    def _count(self, kind: str, row: object) -> None:
        with self.lock:
            (self.hits if row is not None else self.misses)[kind] += 1

    def commit(self) -> None:
        with self.lock:
            try:
                self.conn.commit()
            except sqlite3.OperationalError:
                if not self.best_effort:
                    raise
                self.skipped_writes += 1

    def clear(self) -> None:
        """Forget every cached analysis (positions from your games) and shrink the file."""
        with self.lock:
            self.conn.executescript("DELETE FROM prior_cache; DELETE FROM engine_cache;")
            self.conn.commit()
            self.conn.execute("VACUUM")

    def close(self) -> None:
        self.commit()
        self.conn.close()
