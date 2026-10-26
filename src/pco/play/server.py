"""Localhost app server: the page plus a small JSON API around the Twin, one game at a
time, and background jobs (bringing in games, rebuilding the Twin).

Stdlib only and offline: the page is self-contained (inline SVG board, bundled fonts, no
CDN). The server binds to 127.0.0.1, rejects foreign Host headers (DNS rebinding) and
requires JSON POST bodies (so other websites cannot drive it with simple form posts).
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any

import chess

from pco.evaluation.predictors import position_key
from pco.play.jobs import JobBusy, JobRunner
from pco.play.library import Library, LibraryError, default_workers
from pco.play.opponent import OpponentError
from pco.play.session import GameSession, SessionError
from pco.play.twin import TwinService, numbered, position_id
from pco.presets import PRESETS
from pco.priors.base import PriorRequest

MAX_BODY = 64 * 1024 * 1024  # a PGN of a whole career fits comfortably
FORGET_WORD = "FORGET"
GLIMPSE_RATING = 1500  # "people" on the home page before you have a Twin


def preset_name(config: dict[str, Any]) -> str | None:
    return next((n for n, p in PRESETS.items() if vars(p.analysis) == config), None)


class PlayApp:
    """Thread-safe app state shared by request handlers."""

    def __init__(
        self,
        library: Library,
        prior,
        evaluator,
        cache,
        player=None,
        rng=None,
        temperature: float | None = None,
    ):
        self.library = library
        self.cache = cache
        self.rng = rng or random.Random()
        self.temperature = temperature  # command-line override for every new game
        self.lock = threading.Lock()  # the game
        # The HTTP server uses a new thread per request, but torch/OpenMP thread pools are
        # per calling thread; one persistent inference thread avoids rebuilding them.
        self.inference = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pco-inference")
        self.jobs = JobRunner()
        self.twin = TwinService(None, None, list, prior, evaluator, cache)
        self.session: GameSession | None = None
        self._glimpses: dict[int, chess.Board] = {}
        self._glimpse_id = 0
        self._reset_game()
        self.select(library.pick_player(player))

    def select(self, player: str | None) -> None:
        """Point the app at a player (and their newest Twin, if any)."""
        loader = (lambda: self.library.load_games(player)) if player else list
        self.twin.reload(player, self.library.latest_profile(player), loader)
        self.twin.warm()

    # ----------------------------------------------------------------- the game
    def _reset_game(self) -> None:
        self.version: dict[str, Any] | None = None
        self.analyses: dict[int, tuple] = {}  # ply -> the Twin's view of your decision
        self.memory: dict[str, Any] = {}  # whisper bookkeeping for this game
        self.whisper: dict[str, Any] | None = None
        self.last_choice: dict[str, Any] | None = None
        self.error: str | None = None
        self.saved_pgn: str | None = None
        self.started = self.ended = None
        self._reflection: dict[str, Any] | None = None

    def _game(self) -> GameSession:
        if self.session is None:
            raise SessionError("there is no game; start one")
        return self.session

    def new_game(
        self, version: str = "current", color: str = "white", temperature: float | None = None
    ) -> None:
        if color not in ("white", "black"):
            raise SessionError("color must be 'white' or 'black'")
        if temperature is None:
            temperature = self.temperature
        if temperature is None:
            temperature = self.library.prefs()["temperature"]  # None: the Twin's fitted value
        if temperature is not None and not 0 <= temperature <= 3:
            raise SessionError("temperature must be between 0 and 3")
        try:
            opponent = self.twin.opponent(version, temperature, self.rng)
            view = self.twin.version(version)
        except (ValueError, OpponentError) as exc:
            raise SessionError(str(exc)) from exc
        human = chess.WHITE if color == "white" else chess.BLACK
        self.session = GameSession(opponent, human)
        self._reset_game()
        self.version = {**view, "temperature": opponent.temperature}
        self.started = time.time()

    def human_move(self, uci: str) -> None:
        s = self._game()
        before = s.board.copy()
        s.play_human(uci)
        self.whisper = None
        if before.legal_moves.count() > 1:
            try:  # the Twin's view of the position you just faced: whispers + reflection
                logprobs, engine = self.inference.submit(s.opponent.analyze, before).result()
            except OpponentError:
                pass  # no remark; the game goes on
            else:
                self.cache.commit()  # never hold the cache's write lock between moves
                ply = len(before.move_stack)
                self.analyses[ply] = (logprobs, engine)
                w = self.twin.whisper(before, uci, logprobs, self.version["id"], self.memory)
                self.whisper = w and {**w, "ply": ply}
        self._after_move()

    def opponent_move(self) -> None:
        s = self._game()
        before = s.board.copy()
        try:
            choice = self.inference.submit(s.play_opponent).result()
        except OpponentError as exc:
            self.error = str(exc)  # position unchanged; the page offers a retry
            return
        finally:
            self.cache.commit()
        self.error = None
        self.last_choice = {
            "uci": choice.uci,
            "san": before.san(chess.Move.from_uci(choice.uci)),
            "probability": choice.probability,
            "latency_ms": round(choice.latency_ms, 1),
        }
        self._after_move()

    def resign(self) -> None:
        self._game().resign()
        self._after_move()

    def _after_move(self) -> None:
        s = self.session
        if s.over and self.saved_pgn is None:
            self.ended = time.time()
            played = self.library.settings.played_dir
            played.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            path = played / f"{stamp}-{id(s):x}.pgn"
            path.write_text(s.to_pgn())
            self.saved_pgn = str(path)
            self.cache.commit()

    def reflection(self) -> dict[str, Any]:
        s = self._game()
        if s.over is None:
            raise SessionError("the game is not over")
        if self._reflection is None:
            future = self.inference.submit(self.twin.reflection, s, self.analyses)
            self._reflection = {**future.result(), "version": self.version, "san": s.san_moves()}
        return self._reflection

    def state(self) -> dict[str, Any]:
        s = self.session
        if s is None:
            return {"game": False}
        over = s.over
        last = s.board.move_stack[-1].uci() if s.board.move_stack else None
        legal = [{"uci": u, "san": s.board.san(chess.Move.from_uci(u))} for u in s.legal_moves()]
        return {
            "game": True,
            "fen": s.board.fen(),
            "turn": "white" if s.board.turn == chess.WHITE else "black",
            "human_color": "white" if s.human_color == chess.WHITE else "black",
            "human_to_move": s.human_to_move,
            "opponent_to_move": s.opponent_to_move,
            "legal_moves": s.legal_moves(),
            "legal": legal,  # with SAN, for typed move entry
            "san_moves": s.san_moves(),
            "last_move": last,
            "check": s.board.is_check(),
            "over": None if over is None else {"result": over.result, "reason": over.reason},
            "version": self.version,
            "last_choice": self.last_choice,
            "whisper": self.whisper,
            "error": self.error,
            "saved_pgn": self.saved_pgn,
            "elapsed": round((self.ended or time.time()) - self.started, 1),
        }

    # ----------------------------------------------------------------- views
    def home(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "player": self.twin.player,
            "players": self.library.players(),
            "has_twin": self.twin.has_twin,
            "games": 0,
            "job": self.jobs.view(),
            "game": None,
        }
        if self.twin.player:
            out.update(self.twin.summary())
            out["estimate"] = self.estimate()
        with self.lock:
            s = self.session
            if s is not None and s.over is None and s.board.move_stack:
                out["game"] = {"version": self.version["label"], "plies": len(s.board.move_stack)}
        return out

    def portrait(self) -> dict[str, Any]:
        twin = self.twin
        if not twin.has_twin:
            raise SessionError("there is no Twin yet")
        p = twin.profile
        v = p.validation
        return {
            **twin.summary(),
            "observations": twin.observations(),
            "versions": twin.versions(),
            "model": {
                "rating": p.elo,
                "elo_offset": p.elo_offset,
                "temperature": p.play_temperature,
                "half_life": p.half_life_games,
                "reg_lambda": p.reg_lambda,
                "book_alpha": p.book_alpha,
                "depth": preset_name(p.analysis_config),
                "decisions": sum((p.data.get("decisions") or {}).values()),
                "top1": (v.get("model") or {}).get("top1"),
                "top1_generic": (v.get("generic_maia") or {}).get("top1"),
                "nll": (v.get("model") or {}).get("nll"),
                "nll_generic": (v.get("generic_maia") or {}).get("nll"),
                "prior": p.prior_model,
                "engine": p.engine_id,
                "has_covariance": p.weight_cov is not None,
            },
        }

    def archive(self) -> dict[str, Any]:
        twin = self.twin
        games = twin.games()
        playable = {v["id"] for v in twin.versions()}
        style = twin.style_year if twin.has_twin else None
        by_year: dict[str, list] = {}
        for g in games:
            if g.year:
                by_year.setdefault(g.year, []).append(g)
        years = []
        for year in sorted(by_year, reverse=True):
            rows = by_year[year]
            vid = "current" if year == style else f"year-{year}"
            ratings = [g.elo for g in rows if g.elo]
            first = Counter(
                g.moves[0]
                for g in rows
                if g.white and g.moves and g.start_fen == chess.STARTING_FEN
            )
            tempos = Counter(g.time_class or "unknown" for g in rows)
            years.append(
                {
                    "year": year,
                    "games": len(rows),
                    "rating": int(statistics.median(ratings)) if ratings else None,
                    "signature": twin._signature(first),
                    "tempos": [t for t, _ in tempos.most_common(2)],
                    "version": vid if vid in playable else None,
                    "now": year == style,
                }
            )
        return {"player": twin.player, "years": years}

    def settings_view(self) -> dict[str, Any]:
        lib, p = self.library, self.twin.profile
        return {
            "player": self.twin.player,
            "players": lib.players(),
            "prefs": lib.prefs(),
            "presets": {
                name: {
                    "candidates": pr.analysis.k_max,
                    "nodes": pr.analysis.nodes,
                    "rate": pr.est_decisions_per_s,
                }
                for name, pr in PRESETS.items()
            },
            "accounts": lib.accounts(self.twin.player),
            "storage": lib.storage(),
            "cores": os.cpu_count(),
            "default_workers": default_workers(),
            "twin": None
            if p is None
            else {
                "built": p.created_at,
                "depth": preset_name(p.analysis_config),
                "prior": p.prior_model,
                "engine": p.engine_id,
                "temperature": p.play_temperature,
                "rating": p.elo,
                "elo_offset": p.elo_offset,
                "half_life": p.half_life_games,
            },
            "can_import": lib.http is not None,
            "estimate": self.estimate(),
            "forget_word": FORGET_WORD,
        }

    # ----------------------------------------------------------------- jobs
    def estimate(self) -> dict[str, Any] | None:
        """What a build would cost now (shown before you start one)."""
        player = self.twin.player
        games = self.twin.games() if player else []
        if not games:
            return None
        return self.library.build_plan(player, games, self.twin.profile)

    def _build(self, job, say, player: str):
        plan = self.library.build_plan(player, self.library.load_games(player))
        job.plan = plan["stages"]
        job.meta.update({k: plan[k] for k in ("first", "rereads", "depth", "basis", "decisions")})
        return self.library.build(say, player, self.cache)

    def start_import(
        self, source: str, username: str | None = None, pgn: str | None = None, build: bool = True
    ):
        """Returns (job, joined). Work already under way is joined, never duplicated."""
        running = self.jobs.running()
        if running is not None:
            return running, True
        player = self.twin.player

        def work(job, say):
            result = self.library.import_games(say, player, source, username, pgn)
            job.result.update(result)  # "4,862 games found." while the build runs
            self.select(result["player"])
            if build and (result["new"] > 0 or not self.twin.has_twin):
                profile = self._build(job, say, result["player"])
                self.select(result["player"])
                result["built"] = profile.created_at
            return result

        return self.jobs.start("import", work), False

    def start_build(self):
        """Returns (job, joined). Work already under way is joined, never duplicated."""
        running = self.jobs.running()
        if running is not None:
            return running, True
        player = self.twin.player
        if not player:
            raise SessionError("there are no games yet")

        def work(job, say):
            profile = self._build(job, say, player)
            self.select(player)
            return {"player": player, "built": profile.created_at}

        return self.jobs.start("build", work), False

    # ----------------------------------------------------------------- the home glimpse
    def glimpse(self) -> dict[str, Any]:
        """A real position to answer on the home page: one you have faced many times when
        there is a Twin, else the starting position (answered against people)."""
        found = self.twin.glimpse(self.rng) if self.twin.has_twin else None
        board, seen = found if found else (chess.Board(), 0)
        with self.lock:
            self._glimpse_id += 1
            gid = self._glimpse_id
            self._glimpses[gid] = board
            for old in [k for k in self._glimpses if k <= gid - 16]:
                del self._glimpses[old]
        return {
            "id": gid,
            "mode": "yours" if found else "people",
            "fen": board.fen(),
            "orientation": "white" if board.turn == chess.WHITE else "black",
            "last_move": board.move_stack[-1].uci() if board.move_stack else None,
            "legal": [{"uci": m.uci(), "san": board.san(m)} for m in board.legal_moves],
            "seen": seen,
            "position": position_id(board),
            "number": numbered(board, ""),  # "12." or "12..."
        }

    def glimpse_answer(self, gid: int, uci: str) -> dict[str, Any]:
        with self.lock:
            board = self._glimpses.get(gid)
        if board is None:
            raise SessionError("that position has gone; take another")
        try:
            move = chess.Move.from_uci(uci)
        except ValueError as exc:
            raise SessionError(f"malformed move {uci!r}") from exc
        if move not in board.legal_moves:
            raise SessionError(f"illegal move {uci}")

        def top(logprobs: dict[str, float], k: int = 3) -> list[dict[str, Any]]:
            best = sorted(logprobs, key=logprobs.__getitem__, reverse=True)[:k]
            return [
                {"uci": u, "san": board.san(chess.Move.from_uci(u)), "p": math.exp(logprobs[u])}
                for u in best
            ]

        you = {"uci": uci, "san": board.san(move)}
        if self.twin.has_twin and board.move_stack:
            opp = self.twin.opponent("current", None, self.rng)
            logprobs, _ = self.inference.submit(opp.analyze, board.copy()).result()
            self.cache.commit()
            here = self.twin.profile.book.get(position_key(board), {})
            usual = max(here, key=here.__getitem__) if here else None
            return {
                "mode": "yours",
                "you": {**you, "count": here.get(uci, 0), "p": math.exp(logprobs[uci])},
                "usual": None
                if usual is None
                else {
                    "uci": usual,
                    "san": board.san(chess.Move.from_uci(usual)),
                    "count": here[usual],
                },
                "seen": sum(here.values()),
                "twin": top(logprobs),
            }
        request = PriorRequest(board.copy(), GLIMPSE_RATING, GLIMPSE_RATING)
        dist = self.inference.submit(lambda: self.twin.prior.predict([request])[0]).result()
        return {
            "mode": "people",
            "you": {**you, "p": math.exp(dist.logprobs[uci])},
            "people": top(dist.logprobs),
            "rating": GLIMPSE_RATING,
        }

    def forget(self, what: str, confirm: str) -> None:
        if confirm != FORGET_WORD:
            raise SessionError(f"type {FORGET_WORD} to confirm")
        if self.jobs.busy:
            raise SessionError("wait for the current work to finish")
        with self.lock:
            self.session = None
            self._reset_game()
        player = self.twin.player
        self.library.forget(what, player, self.cache)
        self.select(player if what == "twin" else None)

    def switch_player(self, name: str) -> None:
        if self.jobs.busy:
            raise SessionError("wait for the current work to finish")
        with self.lock:
            self.session = None
            self._reset_game()
        self.select(self.library.pick_player(name))

    def close(self) -> None:
        self.inference.shutdown()


def _page() -> bytes:
    return files("pco.play").joinpath("static/index.html").read_bytes()


# Only bundled font files are served; the pattern rules out traversal by construction.
_STATIC = re.compile(r"^/static/fonts/([a-z0-9-]+\.woff2)$")


def _static(path: str) -> bytes | None:
    m = _STATIC.match(path)
    if m is None:
        return None
    resource = files("pco.play").joinpath("static/fonts").joinpath(m.group(1))
    return resource.read_bytes() if resource.is_file() else None


def make_handler(app: PlayApp, allowed_hosts: set[str]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "pco-play"

        def log_message(self, format: str, *args: Any) -> None:  # keep the terminal quiet
            pass

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if "Cache-Control" not in (extra or {}):
                self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _host_ok(self) -> bool:
            if self.headers.get("Host", "") in allowed_hosts:
                return True
            self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden host"})
            return False

        def _respond(self, compute, game: bool = False) -> None:
            """Run a view/action; app errors become 409 with a message (and the game)."""
            try:
                if game:
                    with app.lock:
                        payload = compute()
                else:
                    payload = compute()
            except (SessionError, LibraryError, JobBusy, ValueError) as exc:
                with app.lock:
                    state = app.state()
                self._json(HTTPStatus.CONFLICT, {"error": str(exc), "state": state})
                return
            except Exception as exc:  # never drop the connection: the page shows the message
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": f"something went wrong: {type(exc).__name__}: {exc}"},
                )
                return
            self._json(HTTPStatus.OK, payload)

        def do_GET(self) -> None:
            if not self._host_ok():
                return
            views = {
                "/api/home": app.home,
                "/api/twin": app.portrait,
                "/api/archive": app.archive,
                "/api/versions": lambda: {"versions": app.twin.versions()},
                "/api/settings": app.settings_view,
                "/api/job": lambda: {"job": app.jobs.view()},
                "/api/glimpse": app.glimpse,
            }
            if self.path == "/":
                self._send(HTTPStatus.OK, _page(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._respond(app.state, game=True)
            elif self.path == "/api/reflection":
                self._respond(app.reflection, game=True)
            elif self.path in views:
                self._respond(views[self.path])
            elif self.path == "/api/pgn":
                with app.lock:
                    pgn = app.session.to_pgn().encode() if app.session else b""
                self._send(
                    HTTPStatus.OK,
                    pgn,
                    "application/x-chess-pgn",
                    {"Content-Disposition": 'attachment; filename="pco-game.pgn"'},
                )
            elif (font := _static(self.path)) is not None:
                self._send(
                    HTTPStatus.OK,
                    font,
                    "font/woff2",
                    {"Cache-Control": "public, max-age=31536000, immutable"},
                )
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._host_ok():
                return
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "JSON body required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > MAX_BODY:
                    raise ValueError("body too large")
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": f"bad request: {exc}"})
                return

            def job_view(started):
                job, joined = started
                return {"job": job.view(), "joined": joined}

            def temperature():
                t = body.get("temperature")
                return None if t is None else float(t)

            game_actions = {
                "/api/new": lambda: app.new_game(
                    str(body.get("version", "current")),
                    str(body.get("color", "white")),
                    temperature(),
                ),
                "/api/move": lambda: app.human_move(str(body.get("uci", ""))),
                "/api/opponent": app.opponent_move,
                "/api/resign": app.resign,
            }
            other_actions = {
                "/api/settings": lambda: {"prefs": app.library.set_prefs(body)},
                "/api/import": lambda: job_view(
                    app.start_import(
                        str(body.get("source", "")),
                        body.get("username"),
                        body.get("pgn"),
                        bool(body.get("build", True)),
                    )
                ),
                "/api/build": lambda: job_view(app.start_build()),
                "/api/glimpse": lambda: app.glimpse_answer(
                    int(body.get("id", 0)), str(body.get("uci", ""))
                ),
                "/api/forget": lambda: (
                    app.forget(str(body.get("what", "")), str(body.get("confirm", ""))),
                    app.home(),
                )[1],
                "/api/player": lambda: (app.switch_player(str(body.get("name", ""))), app.home())[
                    1
                ],
            }
            if self.path in game_actions:
                action = game_actions[self.path]
                self._respond(lambda: (action(), app.state())[1], game=True)
            elif self.path in other_actions:
                self._respond(other_actions[self.path])
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    return Handler


def make_server(app: PlayApp, port: int = 8765) -> ThreadingHTTPServer:
    """Bind on 127.0.0.1 (port 0 picks a free port). Caller runs serve_forever()."""
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    actual = server.server_address[1]
    server.RequestHandlerClass = make_handler(app, {f"127.0.0.1:{actual}", f"localhost:{actual}"})
    return server
