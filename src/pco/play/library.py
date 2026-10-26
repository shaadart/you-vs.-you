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
from pco.importers.sources import SourceError, lichess_game_count, pgn_text_records
from pco.personal.profile import PersonalProfile, train_profile
from pco.play.twin import GameRow
from pco.presets import DEFAULT_RECENT_GAMES, DEFAULT_SPEED, PRESETS, preset
from pco.storage import games as repo
from pco.storage.db import connect

# () -> (prior, make_evaluator, cleanup or None): what a build trains with
Trainer = Callable[[], tuple[Any, Callable[[], Any], Callable[[], None] | None]]
Report = Callable[..., None]  # (stage, detail="", fraction=None)

DEFAULT_PREFS: dict[str, Any] = {"speed": DEFAULT_SPEED, "workers": None, "temperature": None}
# Seconds per decision for each build stage at Lite, 4 workers, on the owner's 12-core
# machine, 65,963 decisions (EXPERIMENTS E8.1). Warm = everything cached. Cold = nothing
# cached: FINDING PATTERNS then pays for the model at every rating it tries (val) and at
# the chosen one (train). Cold analysis itself is priced from the preset's rate instead.
# COMPARING scales with engine nodes.
LITE_NODES = 3_000
STAGE_COST = {
    "warm": {
        "REMEMBERING": 49.2 / 65_963,
        "FINDING PATTERNS": 41.9 / 65_963,
        "COMPARING DECISIONS": 3.4 / 65_963,
        "RECONSTRUCTING YOU": 5.6 / 65_963,
    },
    "cold": {
        "FINDING PATTERNS": 911.1 / 65_963,
        "COMPARING DECISIONS": 39.8 / 65_963,
        "RECONSTRUCTING YOU": 4.8 / 65_963,
    },
}
PLATFORM_NAMES = {"chesscom": "CHESS.COM", "lichess": "LICHESS", "pgn": "PGN"}


class LibraryError(ValueError):
    pass


def build_history(prev: PersonalProfile | None) -> dict[str, Any]:
    """What a previous build says about this machine. Its engine searches computed per
    decision tell how it ran: about 2 when every decision was analyzed (cold), near 0 when
    the analysis was cached (warm). A cold build's REMEMBERING time is the machine's own
    analysis pace; a warm build's stage times describe a rebuild."""
    out: dict[str, Any] = {"decisions": 0, "warm": False, "cold_rate": None, "preset_rate": None}
    if prev is None:
        return out
    decisions = sum((prev.data.get("decisions") or {}).values())
    computed = ((prev.data.get("cache") or {}).get("computed") or {}).get("engine", 0)
    out["decisions"] = max(1, decisions)
    out["warm"] = computed <= 0.2 * decisions
    name = next((n for n, p in PRESETS.items() if vars(p.analysis) == prev.analysis_config), None)
    # profiles saved before per-build counts (2026-10-26) may carry a shared cache's lifetime
    # totals; a "cold" build faster than any measured analysis pace (200/s) cannot be one
    seconds = prev.data.get("train_seconds", 0)
    old_counts = "stage_seconds" not in prev.data  # saved before per-build counts
    if old_counts and not out["warm"] and seconds and decisions / seconds > 200:
        out["warm"] = True
        computed = 0
    if decisions and name and computed >= 1.5 * decisions:
        stages = prev.data.get("stage_seconds") or {}
        rest = sum(per * decisions for per in STAGE_COST["cold"].values())
        seconds = stages.get("REMEMBERING") or (seconds - rest)
        if seconds > 0:
            out["cold_rate"] = decisions / seconds
            out["preset_rate"] = PRESETS[name].est_decisions_per_s
    return out


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
        self._pace_memo: dict[str, Any] = {}

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
                for i, a in enumerate(linked):
                    part = (i, len(linked))
                    reports.append(
                        self._import_account(conn, say, a["platform"], a["username"], player, part)
                    )
            elif source in ("chesscom", "lichess"):
                if not username or not username.strip():
                    raise LibraryError("which username?")
                username = username.strip()
                report = self._import_account(conn, say, source, username, player, (0, 1))
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

    def _import_account(self, conn, say: Report, platform: str, username: str, player, part):
        """One account, reporting how far along it is: Chess.com by monthly archive, Lichess
        by games seen against the account's game count (first imports only)."""
        stage, (i, n) = "FINDING YOUR GAMES", part
        label = f"{PLATFORM_NAMES[platform]} · {username.upper()}"
        say(stage, label, i / n)
        expected = None
        if platform == "lichess" and not repo.last_successful_run(conn, platform, username.lower()):
            expected = lichess_game_count(self.http, username)

        def on_report(r: ImportReport) -> None:
            fraction = (i + min(0.99, r.seen / expected)) / n if expected else None
            say(stage, f"{label} · {r.seen:,} GAMES · {r.new:,} NEW", fraction)

        def on_month(done: int, total: int) -> None:
            say(stage, f"{label} · MONTH {done + 1} OF {total}", (i + done / total) / n)

        report, _ = import_account(
            conn,
            self.http,
            self.settings,
            platform,
            username,
            player=player,
            progress=on_report,
            on_month=on_month,
        )
        return report

    # ------------------------------------------------------------- builds
    def build_plan(
        self, player: str, games: list[GameRow], prev: PersonalProfile | None = None
    ) -> dict[str, Any]:
        """Expected seconds for each build stage, and what the estimate rests on.

        - The last build's measured stage times, scaled to today's decisions, when it used
          the same depth ("last build").
        - Otherwise this machine class's measured costs per decision (EXPERIMENTS E8.1):
          warm when the last build used the same depth (its analysis is cached), cold
          when there was none or the depth changed (every game is read again).
        New decisions are always priced at the depth's measured cold analysis rate."""
        prefs = self.prefs()
        pr = preset(prefs["speed"])
        workers = prefs["workers"] or default_workers()
        dated = [g for g in games if g.played_at]
        window = dated[-DEFAULT_RECENT_GAMES:]
        decisions = sum(g.own_moves() for g in window)
        prev = prev or self.latest_profile(player)
        same = prev is not None and prev.analysis_config == vars(pr.analysis)
        history = build_history(prev)
        # analysis speed for decisions never seen: this machine's own measured pace when an
        # earlier build read everything; otherwise the preset's measured rate (E7.2, 4 workers)
        cold_rate = pr.est_decisions_per_s * workers / 4
        basis = "measured"
        pace = history if history["cold_rate"] else self._machine_pace(player)
        if pace["cold_rate"]:
            cold_rate = pr.est_decisions_per_s * pace["cold_rate"] / pace["preset_rate"]
            basis = "calibrated"
        if same:  # earlier analysis is reused; only newer games are read for the first time
            last_game = prev.data.get("last_game") or ""
            new = sum(g.own_moves() for g in window if g.played_at > last_game)
            cached = decisions - new
            measured = (prev.data.get("stage_seconds") or {}) if history["warm"] else {}
            if measured:
                per = history["decisions"]
                stages = {k: v * decisions / per for k, v in measured.items()}
                stages["REMEMBERING"] = measured.get("REMEMBERING", 0.0) * cached / per
                basis = "last build"
            else:
                warm = STAGE_COST["warm"]
                stages = {k: v * decisions for k, v in warm.items()}
                stages["REMEMBERING"] = warm["REMEMBERING"] * cached
        else:  # first build, or a new depth: every decision is analyzed again
            new = decisions
            # after a depth change the model's results are still cached (they don't depend
            # on depth): only the engine work is new
            base = STAGE_COST["cold"] if prev is None else STAGE_COST["warm"]
            stages = {k: v * decisions for k, v in base.items()}
            cold_compare = STAGE_COST["cold"]["COMPARING DECISIONS"] * decisions
            stages["COMPARING DECISIONS"] = cold_compare * pr.analysis.nodes / LITE_NODES
            stages["REMEMBERING"] = 0.0
        stages["REMEMBERING"] += new / cold_rate
        return {
            "stages": {k: round(v, 1) for k, v in stages.items()},
            "seconds": round(sum(stages.values())),
            "decisions": decisions,
            "new_decisions": new,
            "first": prev is None,
            "rereads": prev is not None and not same,
            "depth": prefs["speed"],
            "basis": basis,
            "rate": round(cold_rate, 1),  # decisions/s for games read for the first time
        }

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

    def _machine_pace(self, player: str) -> dict[str, Any]:
        """The newest build of this player that read every game: this machine's pace."""
        files = sorted((self.settings.profiles_dir / player).glob("profile-*.json"), reverse=True)
        key = tuple(f.name for f in files)
        if self._pace_memo.get("key") != key:
            found = build_history(None)
            for f in files[1:]:  # the newest is `prev`, already examined
                try:
                    found = build_history(PersonalProfile.load(f))
                except (OSError, ValueError):
                    continue
                if found["cold_rate"]:
                    break
            self._pace_memo = {"key": key, "pace": found}
        return self._pace_memo["pace"]

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
