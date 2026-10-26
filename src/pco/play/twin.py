"""The Twin: what the interface knows about "you".

Versions, the portrait (observations), in-game whispers and post-game reflections, all
computed from the player's own games and trained profile. This module returns facts;
wording and pacing belong to the page (docs/DESIGN.md, "Twin").

Versions other than "current" are deliberate approximations: that period's openings (a
book from its own games) and strength (its median rating), played with the current
learned style (residual weights). The page's machine voice says so ("OPENINGS + STRENGTH
· 2024 · STYLE · 2026"); nothing claims a separately trained model.
"""

from __future__ import annotations

import hashlib
import math
import statistics
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

import chess
import numpy as np

from pco.decisions.analysis import cached_search
from pco.decisions.features import clip_cp, phase
from pco.evaluation.predictors import position_key
from pco.personal.opponent import PersonalOpponent
from pco.personal.profile import PersonalProfile

MIN_VERSION_GAMES = 150  # fewer games give a thin book and a noisy rating
TEMPOS = ("bullet", "blitz", "rapid", "classical")
RECENT_DAYS = 365  # time-control versions are you at that tempo over the past year
BOOK_PLIES = 40  # later positions almost never recur: not worth a version book
OBSERVATION_Z = 4.0  # covariances are optimistic (moves within a game correlate)
UNUSUAL_LIKENESS = 0.05  # whisper "That was unusual." below this P(move) / P(top)
FAMILIAR_SEEN = 3  # whisper "That position looks familiar." from this many visits
FAMILIAR_FROM_PLY = 16  # ...but not in the opening, where every position is familiar
MAX_WHISPERS = 3  # per game
WHISPER_GAP = 8  # plies between remarks

PIECES = ("pawn", "knight", "bishop", "rook", "queen", "king")
# weight -> (sentence when you do MORE of it than the human model predicts, when LESS).
# Piece weights are one-hot (only differences between pieces are identified), so they are
# read as contrasts against the average piece.
PHRASES = {
    "castle": ("You castle early.", "You often delay castling."),
    "capture": ("You take material when others wait.", "You often leave a capture for later."),
    "recapture": ("You recapture almost by reflex.", "You don't always recapture straight away."),
    "check": ("You reach for checks.", "You rarely give check just because you can."),
    "advance": ("You play forward.", "You step back to regroup more than most."),
    "engine_best": (
        "You find the precise move more often than most.",
        "You often prefer a natural move to the most precise one.",
    ),
    "wp_loss": ("You play costly moves more often than most.", "You avoid costly moves."),
    "wp_loss_low_clock": (
        "Under time pressure, you let more slip.",
        "Under time pressure, you become more careful.",
    ),
    "cp_loss_endgame": ("In endgames, you take chances.", "In endgames, you become exact."),
    "cp_loss_opening": ("In the opening, you take liberties.", "In the opening, you are exact."),
    "piece_pawn": (
        "You push pawns where others move pieces.",
        "Your pieces lead; your pawns wait.",
    ),
    "piece_knight": ("Knights are where your hands go first.", "Your knights move less than most."),
    "piece_bishop": ("You lean on your bishops.", "Your bishops often wait."),
    "piece_rook": ("Your rooks join early.", "Your rooks wait for open files."),
    "piece_queen": ("Your queen is busy.", "Your queen tends to stay back."),
    "piece_king": ("Your king is unusually active.", "Your king rarely moves unless it must."),
}


@dataclass(frozen=True)
class GameRow:
    id: int
    played_at: str | None
    time_class: str | None
    white: bool  # the player's color
    start_fen: str
    moves: tuple[str, ...]
    elo: int | None  # the player's rating in this game

    @property
    def year(self) -> str | None:
        return self.played_at[:4] if self.played_at else None

    def own_moves(self) -> int:
        white_first = " w " in self.start_fen
        return (len(self.moves) + (white_first == self.white)) // 2


def position_id(board: chess.Board) -> str:
    """A short, stable name for a position (the machine voice: "POSITION 008341")."""
    digest = hashlib.sha1(position_key(board).encode()).hexdigest()
    return f"{int(digest, 16) % 1_000_000:06d}"


def move_kind(board: chess.Board, move: chess.Move) -> tuple[str, str, str, str]:
    """A class of decision: (piece, character, phase, destination), e.g. a quiet knight move
    to f5 in the middlegame."""
    piece = board.piece_at(move.from_square)
    if board.is_castling(move):
        character = "castle"
    elif board.is_capture(move):
        character = "capture"
    elif board.gives_check(move):
        character = "check"
    else:
        character = "quiet"
    name = chess.piece_name(piece.piece_type) if piece else "?"
    return name, character, phase(board), chess.square_name(move.to_square)


def numbered(board: chess.Board, san: str) -> str:
    dots = "." if board.turn == chess.WHITE else "..."
    return f"{board.fullmove_number}{dots}{san}"


class TwinService:
    def __init__(
        self,
        player: str | None,
        profile: PersonalProfile | None,
        games_loader: Callable[[], list[GameRow]],
        prior,
        evaluator,
        cache,
    ):
        self.player, self.profile = player, profile
        self._load_games = games_loader
        self.prior, self.evaluator, self.cache = prior, evaluator, cache
        self._lock = threading.Lock()
        self._memo: dict[str, Any] = {}
        self._computing: dict[str, threading.Lock] = {}
        self._generation = 0

    # ------------------------------------------------------------- data
    def reload(self, player, profile, games_loader) -> None:
        with self._lock:
            self.player, self.profile, self._load_games = player, profile, games_loader
            self._memo.clear()
            self._computing.clear()
            self._generation += 1

    def _get(self, key: str, compute: Callable[[], Any]) -> Any:
        """Memoized derived data; concurrent callers wait for one computation, and a value
        computed for a previous player (before `reload`) is never stored."""
        with self._lock:
            if key in self._memo:
                return self._memo[key]
            gate = self._computing.setdefault(key, threading.Lock())
            generation = self._generation
        with gate:
            with self._lock:
                if key in self._memo:
                    return self._memo[key]
            value = compute()
            with self._lock:
                if generation == self._generation:
                    self._memo[key] = value
            return value

    def games(self) -> list[GameRow]:
        return self._get("games", self._load_games)

    @property
    def has_twin(self) -> bool:
        return self.profile is not None

    def summary(self) -> dict[str, Any]:
        games = self.games()
        p = self.profile
        last_built = (p.data.get("last_game") or "") if p else ""
        return {
            "player": self.player,
            "has_twin": self.has_twin,
            "games": len(games),
            "decisions": sum(g.own_moves() for g in games),
            "years": sorted({g.year for g in games if g.year}),
            "built": p.created_at if p else None,
            "built_games": sum((p.data.get("games") or {}).values()) if p else 0,
            "new_games": sum(1 for g in games if p and (g.played_at or "") > last_built),
            "last_game": max((g.played_at for g in games if g.played_at), default=None),
            "rating": p.elo if p else None,
            "versions": len(self.versions()),
        }

    # ------------------------------------------------------------- versions
    def _slice(self, version: str) -> list[GameRow]:
        if version.startswith("year-"):
            return [g for g in self.games() if g.year == version[5:]]
        if version.startswith("tempo-"):  # you at that time control *now*: the past year
            since = self._recent_cutoff()
            return [
                g
                for g in self.games()
                if g.time_class == version[6:] and (g.played_at or "") >= since
            ]
        raise KeyError(version)

    def _recent_cutoff(self) -> str:
        last = max((g.played_at for g in self.games() if g.played_at), default=None)
        if last is None:
            return ""
        return (datetime.fromisoformat(last[:10]) - timedelta(days=RECENT_DAYS)).strftime(
            "%Y-%m-%d"
        )

    @property
    def style_year(self) -> str:
        p = self.profile
        return (p.data.get("last_game") or p.created_at)[:4]

    def versions(self) -> list[dict[str, Any]]:
        """Current You first; then years and time controls with enough games to meet."""
        if not self.has_twin:
            return []
        return self._get("versions", self._versions)

    def _versions(self) -> list[dict[str, Any]]:
        p, style = self.profile, self.style_year
        out = [
            {
                "id": "current",
                "kind": "current",
                "label": "Current You",
                "period": style,
                "games": sum((p.data.get("games") or {}).values()),
                "rating": p.elo,
                "signature": self._signature(p.book.get(position_key(chess.Board()))),
                "basis": f"STYLE · OPENINGS · STRENGTH · {style}",
            }
        ]
        years = Counter(g.year for g in self.games() if g.year)
        for year in sorted(years, reverse=True):
            if years[year] >= MIN_VERSION_GAMES and year != style:
                out.append(self._version_view(f"year-{year}", "year", f"You · {year}", year))
        for tempo in TEMPOS:
            if len(self._slice(f"tempo-{tempo}")) >= MIN_VERSION_GAMES:
                label = f"{tempo.capitalize()} You"
                out.append(self._version_view(f"tempo-{tempo}", "tempo", label, tempo))
        return out

    def _version_view(self, vid: str, kind: str, label: str, period: str) -> dict[str, Any]:
        games = self._slice(vid)
        first_moves = Counter(
            g.moves[0] for g in games if g.white and g.moves and g.start_fen == chess.STARTING_FEN
        )
        return {
            "id": vid,
            "kind": kind,
            "label": label,
            "period": period,
            "games": len(games),
            "rating": self.rating_for(vid),
            "signature": self._signature(first_moves),
            "basis": f"OPENINGS + STRENGTH · {period.upper()}"
            + (" · PAST YEAR" if kind == "tempo" else "")
            + f" · STYLE · {self.style_year}",
        }

    def version(self, vid: str) -> dict[str, Any]:
        view = next((v for v in self.versions() if v["id"] == vid), None)
        if view is None:
            raise ValueError(f"unknown version {vid!r}")
        return view

    def book_for(self, version: str) -> dict[str, dict[str, int]]:
        if version == "current":
            return self.profile.book
        return self._get(f"book:{version}", lambda: self._book(self._slice(version)))

    @staticmethod
    def _book(games: list[GameRow]) -> dict[str, dict[str, int]]:
        book: dict[str, Counter] = {}
        for g in games:
            board = chess.Board(g.start_fen)
            me = chess.WHITE if g.white else chess.BLACK
            for uci in g.moves[:BOOK_PLIES]:
                if board.turn == me:
                    book.setdefault(position_key(board), Counter())[uci] += 1
                board.push(chess.Move.from_uci(uci))
        return {k: dict(v) for k, v in book.items()}

    def rating_for(self, version: str) -> int:
        if version == "current":
            return self.profile.elo
        ratings = [g.elo for g in self._slice(version) if g.elo]
        return int(statistics.median(ratings)) if ratings else self.profile.elo

    @staticmethod
    def _signature(first_moves) -> dict[str, Any] | None:
        """The most common first move as White, e.g. {"move": "1.e4", "share": 0.87}."""
        if not first_moves:
            return None
        uci, n = max(first_moves.items(), key=lambda kv: kv[1])
        san = chess.Board().san(chess.Move.from_uci(uci))
        return {"move": f"1.{san}", "share": n / sum(first_moves.values())}

    def opponent(self, version: str, temperature: float | None, rng) -> PersonalOpponent:
        if not self.has_twin:
            raise ValueError("there is no Twin yet")
        self.version(version)  # validates the id
        profile = replace(self.profile, book=self.book_for(version), elo=self.rating_for(version))
        return PersonalOpponent(profile, self.prior, self.evaluator, self.cache, temperature, rng)

    # ------------------------------------------------------------- whispers
    def seen(self, board: chess.Board, version: str) -> int:
        """How often you stood in this exact position (your move) in that version's games."""
        counts = self.book_for(version).get(position_key(board))
        return sum(counts.values()) if counts else 0

    @staticmethod
    def familiarity(logprobs: dict[str, float], uci: str) -> dict[str, float]:
        ranked = sorted(logprobs, key=logprobs.__getitem__, reverse=True)
        p, top = math.exp(logprobs[uci]), math.exp(logprobs[ranked[0]])
        return {"p": p, "likeness": p / top, "rank": ranked.index(uci) + 1}

    def whisper(
        self,
        board: chess.Board,
        uci: str,
        logprobs: dict[str, float],
        version: str,
        memory: dict[str, Any],
    ) -> dict[str, Any] | None:
        """At most one quiet remark about your move `uci` in `board`; `memory` is per game."""
        ply = len(board.move_stack)
        if (
            memory.setdefault("count", 0) >= MAX_WHISPERS
            or memory.get("last_ply", -99) > ply - WHISPER_GAP  # never two remarks close together
            or board.legal_moves.count() < 2
        ):
            return None
        memory.setdefault("in_book", True)
        seen = self.seen(board, version)
        pid = position_id(board)
        fam = self.familiarity(logprobs, uci)
        out = None
        if memory["in_book"] and seen == 0 and board.fullmove_number > 1:
            memory["in_book"] = False
            out = {"kind": "new", "position": pid, "seen": 0}
        elif fam["likeness"] < UNUSUAL_LIKENESS:
            out = {"kind": "unusual", "position": pid, **fam}
        elif seen >= FAMILIAR_SEEN and ply >= FAMILIAR_FROM_PLY and not memory.get("familiar"):
            memory["familiar"] = True
            out = {"kind": "familiar", "position": pid, "seen": seen}
        if out:
            memory["count"] += 1
            memory["last_ply"] = ply
        return out

    # ------------------------------------------------------------- kinds of decision
    def kind_counts(self) -> Counter:
        """How often you made each kind of decision, over all your games."""
        return self._get("kinds", self._kind_counts)

    def _kind_counts(self) -> Counter:
        counts: Counter = Counter()
        for g in self.games():
            board = chess.Board(g.start_fen)
            me = chess.WHITE if g.white else chess.BLACK
            for uci in g.moves:
                move = chess.Move.from_uci(uci)
                if board.turn == me:
                    counts[move_kind(board, move)] += 1
                board.push(move)
        return counts

    def warm(self) -> None:
        """Precompute slow derived data in the background (games, versions, decision kinds)."""
        if self.has_twin:
            threading.Thread(
                target=lambda: (self.versions(), self.kind_counts()), daemon=True
            ).start()

    # ------------------------------------------------------------- portrait
    def observations(self, limit: int = 4) -> list[dict[str, Any]]:
        """Clear, stable differences between you and the human model the Twin starts from.

        Only weights (or piece contrasts) with |z| >= OBSERVATION_Z under the fitted
        covariance; a profile built before covariances were recorded gives none. Opening
        facts come from your own move counts."""
        p = self.profile
        if p is None:
            return []
        out = []
        if p.weight_cov is not None:
            names, w, cov = p.feature_names, np.array(p.weights), np.array(p.weight_cov)
            index = {n: i for i, n in enumerate(names)}
            pieces = [index[f"piece_{k}"] for k in PIECES]
            found = []
            for name in PHRASES:
                a = np.zeros(len(names))
                if name.startswith("piece_"):
                    a[pieces] = -1 / len(pieces)
                a[index[name]] += 1
                value, var = float(a @ w), float(a @ cov @ a)
                if var > 0 and abs(value) / math.sqrt(var) >= OBSERVATION_Z:
                    found.append((abs(value) / math.sqrt(var), name, value))
            for z, name, value in sorted(found, reverse=True)[:limit]:
                out.append(
                    {
                        "text": PHRASES[name][0 if value > 0 else 1],
                        "machine": f"{name.upper()} {value:+.2f} · Z {z:.1f}",
                    }
                )
        out.extend(self._opening_facts())
        return out

    def _opening_facts(self) -> list[dict[str, Any]]:
        book, start = self.profile.book, chess.Board()
        facts = []
        counts = book.get(position_key(start))
        first = self._signature(counts)
        if first and first["share"] >= 0.4:
            facts.append(
                {
                    "text": f"As White, you begin with {first['move']}"
                    f" in {first['share']:.0%} of your games.",
                    "machine": f"POSITION {position_id(start)} · SEEN {sum(counts.values())}×",
                }
            )
        after = chess.Board()
        after.push_san("e4")
        replies = book.get(position_key(after))
        if replies and sum(replies.values()) >= 20:
            uci, n = max(replies.items(), key=lambda kv: kv[1])
            share = n / sum(replies.values())
            if share >= 0.4:
                san = after.san(chess.Move.from_uci(uci))
                facts.append(
                    {
                        "text": f"Against 1.e4, you answer 1...{san} in {share:.0%} of your games.",
                        "machine": f"POSITION {position_id(after)} · SEEN {sum(replies.values())}×",
                    }
                )
        return facts

    # ------------------------------------------------------------- reflection
    @staticmethod
    def _cp(opp: PersonalOpponent, board: chess.Board, uci: str, engine) -> int | None:
        if engine is None:
            return None
        ev = engine.evals.get(uci)
        if ev is None:  # outside the live set: scored on its own, as in training
            ev = cached_search(opp.evaluator, opp.cache, board.fen(), opp.config.nodes, (uci,))[0]
        return clip_cp(ev.cp)

    def reflection(self, session, analyses: dict[int, tuple]) -> dict[str, Any]:
        """Compare each of your decisions in `session` with what the Twin would have chosen.

        `analyses` maps ply -> (logprobs, engine) computed during the game; missing plies
        are analyzed now. Runs engine searches: call it on the inference thread."""
        opp = session.opponent
        human = session.human_color
        board = chess.Board(session.start_fen)
        positions, decisions = [board.fen()], []
        kinds = self.kind_counts()
        book = opp.profile.book  # this version's own move counts per position
        for ply, move in enumerate(list(session.board.move_stack)):
            if board.turn == human and board.legal_moves.count() > 1:
                logprobs, engine = analyses.get(ply) or opp.analyze(board)
                uci = move.uci()
                fam = self.familiarity(logprobs, uci)
                top = max(logprobs, key=logprobs.__getitem__)
                twin_move = chess.Move.from_uci(top)
                yours, its = move_kind(board, move), move_kind(board, twin_move)
                here = book.get(position_key(board), {})
                decisions.append(
                    {
                        "ply": ply,
                        "fen": board.fen(),
                        "position": position_id(board),
                        "human": uci,
                        "human_san": numbered(board, board.san(move)),
                        "twin": top,
                        "twin_san": numbered(board, board.san(twin_move)),
                        "p_human": fam["p"],
                        "p_twin": math.exp(logprobs[top]),
                        "rank": fam["rank"],
                        "cp_human": self._cp(opp, board, uci, engine),
                        "cp_twin": self._cp(opp, board, top, engine),
                        "eval": clip_cp(engine.best.cp) if engine else None,
                        "kind_human": list(yours),
                        "kind_human_count": kinds.get(yours, 0),
                        "kind_twin": list(its),
                        "kind_twin_count": kinds.get(its, 0),
                        # how often you played each move in this exact position
                        "here_human": here.get(uci, 0),
                        "here_twin": here.get(top, 0),
                        "here_total": sum(here.values()),
                    }
                )
            board.push(move)
            positions.append(board.fen())
        over = session.over
        if over is None:
            outcome = "unfinished"
        elif over.reason == "resignation":
            outcome = "resigned"
        elif over.result == "1/2-1/2":
            outcome = "draw"
        else:
            outcome = "won" if (over.result == "1-0") == (human == chess.WHITE) else "lost"
        n = len(decisions)
        evals = [d["eval"] for d in decisions if d["eval"] is not None]
        divergent = sorted(
            (d for d in decisions if d["human"] != d["twin"]),
            key=lambda d: d["p_twin"] - d["p_human"],
            reverse=True,
        )[:5]
        for d in divergent:
            gap = None if None in (d["cp_human"], d["cp_twin"]) else d["cp_human"] - d["cp_twin"]
            if min(d["p_human"], d["p_twin"]) >= 0.1 or (gap is not None and abs(gap) <= 50):
                d["verdict"] = "both"  # both plausible: neither rare nor clearly worse
            elif gap is None:
                d["verdict"] = "unknown"
            else:
                d["verdict"] = "yours" if gap > 0 else "its"
        usual = (self.profile.validation.get("model") or {}).get("top3") if self.profile else None
        return {
            "outcome": outcome,
            "reason": over.reason if over else None,
            "decisions": n,
            # share of your moves among the Twin's three likeliest, and the same share on
            # your own recent real games (validation): "very you" is relative to you
            "match": sum(d["rank"] <= 3 for d in decisions) / n if n else None,
            "differed": sum(d["rank"] > 1 for d in decisions),  # not the Twin's first choice
            "usual_match": usual,
            "lowest_eval": min(evals) if evals else None,  # your worst moment (your view, cp)
            "highest_eval": max(evals) if evals else None,
            "positions": positions,
            "moves": [m.uci() for m in session.board.move_stack],
            "human_color": "white" if human == chess.WHITE else "black",
            "divergences": sorted(divergent, key=lambda d: d["ply"]),
        }
