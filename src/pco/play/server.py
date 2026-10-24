"""Localhost play server: one page + a small JSON API around a single GameSession.

Stdlib only and offline: the page is self-contained (inline SVG board, no CDN). The
server binds to 127.0.0.1, rejects foreign Host headers (DNS rebinding) and requires
JSON POST bodies (so other websites cannot drive it with simple form posts).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any

import chess

from pco.play.opponent import DEFAULT_PLAY_TEMPERATURE, Opponent, OpponentError
from pco.play.session import GameSession, SessionError

OpponentFactory = Callable[[int, float], Opponent]  # (elo, temperature) -> Opponent


class PlayApp:
    """Thread-safe game state shared by request handlers."""

    def __init__(
        self,
        make_opponent: OpponentFactory,
        played_dir: Path,
        elo: int = 1500,
        temperature: float = DEFAULT_PLAY_TEMPERATURE,
    ):
        self.make_opponent = make_opponent
        self.played_dir = played_dir
        self.lock = threading.Lock()
        # The HTTP server uses a new thread per request, but torch/OpenMP thread pools are
        # per calling thread; one persistent inference thread avoids rebuilding them.
        self.inference = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pco-inference")
        self.last_choice: dict[str, Any] | None = None
        self.error: str | None = None
        self.saved_pgn: str | None = None
        self.new_game("white", elo, temperature)

    # ----------------------------------------------------------------- actions

    def new_game(self, color: str, elo: int, temperature: float) -> None:
        if color not in ("white", "black"):
            raise SessionError("color must be 'white' or 'black'")
        if not 0 <= elo <= 4000:
            raise SessionError("elo must be between 0 and 4000")
        if not 0 <= temperature <= 3:
            raise SessionError("temperature must be between 0 and 3")
        human = chess.WHITE if color == "white" else chess.BLACK
        self.session = GameSession(self.make_opponent(elo, temperature), human)
        self.elo, self.temperature = elo, temperature
        self.last_choice = self.error = self.saved_pgn = None

    def human_move(self, uci: str) -> None:
        self.session.play_human(uci)
        self._after_move()

    def opponent_move(self) -> None:
        before = self.session.board.copy()
        try:
            choice = self.inference.submit(self.session.play_opponent).result()
        except OpponentError as exc:
            self.error = str(exc)  # position unchanged; the page offers a retry
            return
        self.error = None
        self.last_choice = {
            "uci": choice.uci,
            "san": before.san(chess.Move.from_uci(choice.uci)),
            "probability": choice.probability,
            "top": [
                {"uci": u, "san": before.san(chess.Move.from_uci(u)), "probability": p}
                for u, p in choice.top
            ],
            "latency_ms": round(choice.latency_ms, 1),
            "source": choice.source,
            "likeness": choice.likeness,
        }
        self._after_move()

    def resign(self) -> None:
        self.session.resign()
        self._after_move()

    def _after_move(self) -> None:
        if self.session.over and self.saved_pgn is None:
            self.played_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            path = self.played_dir / f"{stamp}-{id(self.session):x}.pgn"
            path.write_text(self.session.to_pgn())
            self.saved_pgn = str(path)

    # ----------------------------------------------------------------- view

    def state(self) -> dict[str, Any]:
        s = self.session
        over = s.over
        last = s.board.move_stack[-1].uci() if s.board.move_stack else None
        return {
            "fen": s.board.fen(),
            "turn": "white" if s.board.turn == chess.WHITE else "black",
            "human_color": "white" if s.human_color == chess.WHITE else "black",
            "human_to_move": s.human_to_move,
            "opponent_to_move": s.opponent_to_move,
            "legal_moves": s.legal_moves(),
            "san_moves": s.san_moves(),
            "last_move": last,
            "check": s.board.is_check(),
            "over": None if over is None else {"result": over.result, "reason": over.reason},
            "opponent": {
                "label": s.opponent.label,
                "personal": getattr(s.opponent, "profile", None) is not None,
                "elo": self.elo,
                "temperature": self.temperature,
            },
            "last_choice": self.last_choice,
            "error": self.error,
            "saved_pgn": self.saved_pgn,
        }


def _page() -> bytes:
    return files("pco.play").joinpath("static/index.html").read_bytes()


def make_handler(app: PlayApp, allowed_hosts: set[str]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "pco-play"

        def log_message(self, format: str, *args: Any) -> None:  # keep the terminal quiet
            pass

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _host_ok(self) -> bool:
            if self.headers.get("Host", "") in allowed_hosts:
                return True
            self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden host"})
            return False

        def do_GET(self) -> None:
            if not self._host_ok():
                return
            if self.path == "/":
                self._send(HTTPStatus.OK, _page(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                with app.lock:
                    self._json(HTTPStatus.OK, app.state())
            elif self.path == "/api/pgn":
                with app.lock:
                    pgn = app.session.to_pgn().encode()
                self._send(
                    HTTPStatus.OK,
                    pgn,
                    "application/x-chess-pgn",
                    {"Content-Disposition": 'attachment; filename="pco-game.pgn"'},
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
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": f"bad request: {exc}"})
                return
            actions = {
                "/api/new": lambda: app.new_game(
                    str(body.get("color", "white")),
                    int(body.get("elo", app.elo)),
                    float(body.get("temperature", app.temperature)),
                ),
                "/api/move": lambda: app.human_move(str(body.get("uci", ""))),
                "/api/opponent": app.opponent_move,
                "/api/resign": app.resign,
            }
            action = actions.get(self.path)
            if action is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            with app.lock:
                try:
                    action()
                except (SessionError, ValueError) as exc:
                    self._json(HTTPStatus.CONFLICT, {"error": str(exc), "state": app.state()})
                    return
                self._json(HTTPStatus.OK, app.state())

    return Handler


def make_server(app: PlayApp, port: int = 8765) -> ThreadingHTTPServer:
    """Bind on 127.0.0.1 (port 0 picks a free port). Caller runs serve_forever()."""
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    actual = server.server_address[1]
    server.RequestHandlerClass = make_handler(app, {f"127.0.0.1:{actual}", f"localhost:{actual}"})
    return server
