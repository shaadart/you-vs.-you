"""The interface's view of $PCO_HOME: players, games, Twins, preferences, imports, builds
and forgetting.

The page does what the CLI does through the same code paths (`import_account`,
`import_records`, `train_profile`), so it keeps the same guarantees: imports are
repeatable and incremental, training is cached and resumable, nothing leaves the machine
except the requests to Chess.com/Lichess that the player starts.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import chess.pgn

from pco.assets import MAIA3_5M, ensure_asset, stockfish_binary
from pco.config import Settings
from pco.importers.accounts import import_account
from pco.importers.http import Http
from pco.importers.pipeline import ImportReport, import_records
from pco.importers.sources import SourceError, pgn_text_records
from pco.personal.profile import PersonalProfile, train_profile
from pco.play.twin import GameRow
from pco.presets import DEFAULT_RECENT_GAMES, DEFAULT_SPEED, PRESETS, preset
from pco.storage import games as repo
from pco.storage.db import connect

# () -> (prior, make_evaluator, cleanup or None): what a build trains with
Trainer = Callable[[], tuple[Any, Callable[[], Any], Callable[[], None] | None]]
Report = Callable[[str, str], None]

DEFAULT_PREFS: dict[str, Any] = {"speed": DEFAULT_SPEED, "workers": None, "temperature": None}
PLATFORM_NAMES = {"chesscom": "CHESS.COM", "lichess": "LICHESS", "pgn": "PGN"}


class LibraryError(ValueError):
    pass


def real_trainer(settings: Settings) -> Trainer:
    """Builds train with their own Maia (more torch threads) and Stockfish workers."""

    def make():
        import torch

        from pco.analysis.stockfish import StockfishEvaluator
        from pco.priors.maia3 import Maia3Prior

        prior = Maia3Prior(
            ensure_asset(settings, MAIA3_5M, offline=True), threads=settings.torch_threads
        )
        sf = stockfish_binary(settings)
        # torch's thread count is process-wide: give live play its own setting back
        return (
            prior,
            lambda: StockfishEvaluator(sf),
            lambda: torch.set_num_threads(settings.live_threads),
        )

    return make


def default_workers() -> int:
    return max(1, min(4, (os.cpu_count() or 2) - 2))


def most_common_name(pgn_text: str) -> str | None:
    """The name that appears in most games of a PGN: almost always its owner."""
    names: Counter = Counter()
    for record in pgn_text_records(pgn_text):
        headers = chess.pgn.read_headers(io.StringIO(record.text))
        if headers:
            for side in ("White", "Black"):
                name = headers.get(side, "").strip()
                if name and name != "?":
                    names[name] += 1
    return names.most_common(1)[0][0] if names else None


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return 0


class Library:
    def __init__(
        self, settings: Settings, http: Http | None = None, trainer: Trainer | None = None
    ):
        self.settings = settings
        self.http = http
        self._trainer = trainer

    def _connect(self):
        return connect(self.settings.db_path)

    # ------------------------------------------------------------- players & games
    def players(self) -> list[str]:
        if not self.settings.db_path.exists():
            return []
        conn = self._connect()
        try:
            return [r[0] for r in conn.execute("SELECT name FROM players ORDER BY name")]
        finally:
            conn.close()

    def pick_player(self, requested: str | None = None) -> str | None:
        """The requested player; else the one with the newest Twin; else the most games."""
        players = self.players()
        if requested:
            if requested not in players:
                raise LibraryError(f"no player named {requested!r}")
            return requested
        built = [
            (max(files).name, name)
            for name in players
            if (files := list((self.settings.profiles_dir / name).glob("profile-*.json")))
        ]
        if built:
            return max(built)[1]
        counts = [(len(self.load_games(name)), name) for name in players]
        return max(counts)[1] if counts else None

    def load_games(self, player: str) -> list[GameRow]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT pg.id, pg.played_at, pg.time_class, pg.color, pg.start_fen, pg.moves_uci,"
                " pg.white_elo, pg.black_elo FROM player_games pg"
                " JOIN players p ON p.id = pg.player_id WHERE p.name = ?"
                " ORDER BY pg.played_at IS NULL, pg.played_at, pg.id",
                (player,),
            ).fetchall()
        finally:
            conn.close()
        return [
            GameRow(
                id=r["id"],
                played_at=r["played_at"],
                time_class=r["time_class"],
                white=r["color"] == "white",
                start_fen=r["start_fen"],
                moves=tuple(r["moves_uci"].split()),
                elo=r["white_elo"] if r["color"] == "white" else r["black_elo"],
            )
            for r in rows
        ]

    def latest_profile(self, player: str | None) -> PersonalProfile | None:
        """The player's newest Twin; None when there is none or it predates this version."""
        if not player:
            return None
        try:
            return PersonalProfile.latest(self.settings.profiles_dir, player)
        except (FileNotFoundError, ValueError):
            return None

    def accounts(self, player: str | None) -> list[dict[str, Any]]:
        if not player or not self.settings.db_path.exists():
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT a.platform, a.username FROM accounts a JOIN players p"
                " ON p.id = a.player_id WHERE p.name = ? ORDER BY a.platform",
                (player,),
            ).fetchall()
            return [
                {
                    "platform": r["platform"],
                    "username": r["username"],
                    "last_import": repo.last_successful_run(
                        conn, r["platform"], r["username"].lower()
                    ),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def _count_games(self, conn, player: str) -> int:
        return conn.execute(
            "SELECT count(*) FROM player_games pg JOIN players p ON p.id = pg.player_id"
            " WHERE p.name = ?",
            (player,),
        ).fetchone()[0]

    # ------------------------------------------------------------- preferences
    @property
    def _prefs_path(self) -> Path:
        return self.settings.home / "preferences.json"

    def prefs(self) -> dict[str, Any]:
        try:
            stored = json.loads(self._prefs_path.read_text())
        except (OSError, ValueError):
            stored = {}
        return {k: stored.get(k, v) for k, v in DEFAULT_PREFS.items()}

    def set_prefs(self, changes: dict[str, Any]) -> dict[str, Any]:
        prefs = self.prefs()
        for key, value in changes.items():
            if key == "speed" and value not in PRESETS:
                raise LibraryError(f"unknown analysis depth {value!r}")
            if key == "workers" and value is not None:
                if not isinstance(value, int) or not 1 <= value <= (os.cpu_count() or 1):
                    raise LibraryError("workers must be between 1 and the number of cores")
            if key == "temperature" and value is not None:
                if not isinstance(value, int | float) or not 0.1 <= value <= 1.5:
                    raise LibraryError("temperature must be between 0.1 and 1.5")
            if key not in DEFAULT_PREFS:
                raise LibraryError(f"unknown preference {key!r}")
            prefs[key] = value
        self.settings.home.mkdir(parents=True, exist_ok=True)
        self._prefs_path.write_text(json.dumps(prefs, indent=2))
        return prefs

    # ------------------------------------------------------------- imports
    def import_games(
        self,
        say: Report,
        player: str | None,
        source: str,
        username: str | None = None,
        pgn_text: str | None = None,
    ) -> dict[str, Any]:
        """Import from an account ('chesscom'/'lichess'), PGN text, or 'accounts' (every
        account already linked to `player`, incrementally). Returns who and how many."""
        stage = "FINDING YOUR GAMES"
        conn = self._connect()

        def progress(label: str):
            return lambda r: say(stage, f"{label} · {r.seen:,} GAMES · {r.new:,} NEW")

        try:
            reports: list[ImportReport] = []
            if source == "accounts":
                linked = self.accounts(player)
                if not linked:
                    raise LibraryError("no Chess.com or Lichess account is linked yet")
                for a in linked:
                    label = f"{PLATFORM_NAMES[a['platform']]} · {a['username'].upper()}"
                    say(stage, label)
                    report, _ = import_account(
                        conn,
                        self.http,
                        self.settings,
                        a["platform"],
                        a["username"],
                        player=player,
                        progress=progress(label),
                    )
                    reports.append(report)
            elif source in ("chesscom", "lichess"):
                if not username or not username.strip():
                    raise LibraryError("which username?")
                username = username.strip()
                label = f"{PLATFORM_NAMES[source]} · {username.upper()}"
                say(stage, label)
                report, _ = import_account(
                    conn,
                    self.http,
                    self.settings,
                    source,
                    username,
                    player=player,
                    progress=progress(label),
                )
                reports.append(report)
                if (
                    report.involving_player == 0
                    and repo.account_owner(conn, source, username) is None
                ):
                    raise LibraryError(f"no games found for {username!r}")
                player = player or repo.account_owner(conn, source, username)
            elif source == "pgn":
                if not pgn_text or not pgn_text.strip():
                    raise LibraryError("the file is empty")
                username = (username or "").strip() or most_common_name(pgn_text)
                if not username:
                    raise LibraryError("no player names in this PGN")
                digest = hashlib.sha256(pgn_text.encode()).hexdigest()[:12]
                say(stage, f"PGN · {len(pgn_text) // 1024:,} KB · {username.upper()}")
                report = import_records(
                    conn,
                    "pgn",
                    f"upload:{digest}",
                    pgn_text_records(pgn_text),
                    username=username,
                    player=player,
                    progress=progress("PGN"),
                )
                reports.append(report)
                if report.involving_player == 0:
                    raise LibraryError(f"{username!r} does not play in these games")
                player = player or username
            else:
                raise LibraryError(f"unknown source {source!r}")
        except (SourceError, repo.AccountConflict) as exc:
            raise LibraryError(str(exc)) from exc
        finally:
            conn.close()
        conn = self._connect()
        try:
            found = self._count_games(conn, player)
        finally:
            conn.close()
        new = sum(r.new for r in reports)
        say(stage, f"{found:,} GAMES · {new:,} NEW")
        return {"player": player, "found": found, "new": new}

    # ------------------------------------------------------------- builds
    def build(self, say: Report, player: str, cache) -> PersonalProfile:
        """Train and save a new Twin with the preferred depth (cached work is reused)."""
        if self._trainer is None:
            raise LibraryError("building is not available here")
        prefs = self.prefs()
        prior, make_evaluator, cleanup = self._trainer()
        conn = self._connect()
        try:
            profile = train_profile(
                conn,
                player,
                prior,
                make_evaluator,
                cache,
                config=preset(prefs["speed"]).analysis,
                workers=prefs["workers"] or default_workers(),
                previous=self.latest_profile(player),
                recent_games=DEFAULT_RECENT_GAMES,
                report=say,
            )
        finally:
            conn.close()
            if cleanup:
                cleanup()
        profile.save(self.settings.profiles_dir)
        say("READY", f"SAVED · {profile.created_at}")
        return profile

    # ------------------------------------------------------------- data
    def storage(self) -> dict[str, Any]:
        s = self.settings
        db = sum(_size(Path(f"{s.db_path}{suffix}")) for suffix in ("", "-wal", "-shm"))
        played = list(s.played_dir.glob("*.pgn")) if s.played_dir.exists() else []
        return {
            "home": str(s.home),
            "games_bytes": db,
            "analysis_bytes": _size(s.cache_dir),
            "twin_bytes": _size(s.profiles_dir),
            "played_games": len(played),
            "played_bytes": sum(p.stat().st_size for p in played),
        }

    def forget(self, what: str, player: str | None, cache) -> None:
        """'twin': delete the player's Twins (games stay). 'everything': games, Twins,
        analysis, played games, reports and preferences (downloaded models stay)."""
        s = self.settings
        if what == "twin":
            if player:
                shutil.rmtree(s.profiles_dir / player, ignore_errors=True)
        elif what == "everything":
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(f"{s.db_path}{suffix}").unlink(missing_ok=True)
            for directory in (s.profiles_dir, s.played_dir, s.reports_dir):
                shutil.rmtree(directory, ignore_errors=True)
            self._prefs_path.unlink(missing_ok=True)
            cache.clear()
        else:
            raise LibraryError(f"cannot forget {what!r}")
