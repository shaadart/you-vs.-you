"""Decision episodes: the player's own decisions, with only pre-move information.

An episode is *observed* data reconstructed from a stored game. Leakage rules:
nothing after the decision is used (no later moves, no result, no post-move clock),
and ratings missing from a game are filled only from the player's *earlier* games.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache

import chess

HISTORY_PLIES = 7  # predecessors kept: with the current position, Maia-3's 8-position window
DEFAULT_ELO = 1500  # used only when the player has no known rating yet (flagged)


@dataclass(frozen=True)
class DecisionEpisode:
    game_id: int
    game_index: int  # chronological index of the game among the player's games
    ply: int  # index of this decision in the game's move list
    color: str  # 'white' | 'black'
    platform: str
    played_at: str | None
    time_class: str | None
    tc_base_s: int | None
    tc_inc_s: int | None
    self_elo: int
    oppo_elo: int
    self_elo_source: str  # 'game' | 'carried' (earlier game) | 'default'
    oppo_elo_source: str  # 'game' | 'self'
    clock_self_s: float | None  # mover's clock before this move
    clock_oppo_s: float | None  # opponent's clock at this moment
    prev_think_s: float | None  # time the mover spent on their previous move
    root_fen: str  # position len(recent_moves) plies before the decision
    recent_moves: tuple[str, ...]  # moves from root_fen up to the decision position
    actual_move: str  # the move the player chose: a training label, never an input

    def board(self) -> chess.Board:
        """Decision position, with the recent moves on its stack (history for the prior).

        Moves come from games whose legality was verified at import (and re-checkable with
        `pco games --verify`), so they are pushed without re-validation: this is the hot
        path of building and training (EXPERIMENTS E6.1)."""
        return _board(self.root_fen, self.recent_moves).copy()


@lru_cache(maxsize=65_536)
def _board(root_fen: str, recent_moves: tuple[str, ...]) -> chess.Board:
    """Memoized reconstruction (callers get copies, so the cached board is never mutated)."""
    board = chess.Board(root_fen)
    for uci in recent_moves:
        board.push(chess.Move.from_uci(uci))
    return board


def _clock_before(clocks: list | None, ply: int, base: int | None) -> float | None:
    """Clock of the side making move `ply`, before making it."""
    if clocks is None:
        return None
    if ply >= 2:
        return clocks[ply - 2]
    return float(base) if base is not None else None


def _opponent_clock(clocks: list | None, ply: int, base: int | None) -> float | None:
    if clocks is None:
        return None
    if ply >= 1:
        return clocks[ply - 1]
    return float(base) if base is not None else None


def _prev_think(clocks: list | None, ply: int, inc: int | None) -> float | None:
    """Own previous move's thinking time: clock before it - clock after it + increment."""
    if clocks is None or ply < 4 or clocks[ply - 4] is None or clocks[ply - 2] is None:
        return None
    return max(0.0, clocks[ply - 4] - clocks[ply - 2] + (inc or 0))


def player_games(conn: sqlite3.Connection, player: str, max_games: int | None = None) -> list:
    rows = conn.execute(
        "SELECT pg.* FROM player_games pg JOIN players p ON p.id = pg.player_id"
        " WHERE p.name = ? ORDER BY pg.played_at IS NULL, pg.played_at, pg.id",
        (player,),
    ).fetchall()
    return rows[:max_games] if max_games else rows


def episodes_for_game(
    row: sqlite3.Row, game_index: int, carried_elo: int | None
) -> list[DecisionEpisode]:
    color = chess.WHITE if row["color"] == "white" else chess.BLACK
    self_elo = row["white_elo"] if color == chess.WHITE else row["black_elo"]
    oppo_elo = row["black_elo"] if color == chess.WHITE else row["white_elo"]
    if self_elo is not None:
        resolved_self, self_src = self_elo, "game"
    elif carried_elo is not None:
        resolved_self, self_src = carried_elo, "carried"
    else:
        resolved_self, self_src = DEFAULT_ELO, "default"
    resolved_oppo, oppo_src = (
        (oppo_elo, "game") if oppo_elo is not None else (resolved_self, "self")
    )
    clocks = json.loads(row["clocks_s"]) if row["clocks_s"] else None
    moves = row["moves_uci"].split()

    out = []
    board = chess.Board(row["start_fen"])
    fens = [board.fen()]  # fens[i] = position before move i
    for ply, uci in enumerate(moves):
        if board.turn == color:
            start = max(0, ply - HISTORY_PLIES)
            out.append(
                DecisionEpisode(
                    game_id=row["id"],
                    game_index=game_index,
                    ply=ply,
                    color=row["color"],
                    platform=row["platform"],
                    played_at=row["played_at"],
                    time_class=row["time_class"],
                    tc_base_s=row["tc_base_s"],
                    tc_inc_s=row["tc_inc_s"],
                    self_elo=resolved_self,
                    oppo_elo=resolved_oppo,
                    self_elo_source=self_src,
                    oppo_elo_source=oppo_src,
                    clock_self_s=_clock_before(clocks, ply, row["tc_base_s"]),
                    clock_oppo_s=_opponent_clock(clocks, ply, row["tc_base_s"]),
                    prev_think_s=_prev_think(clocks, ply, row["tc_inc_s"]),
                    root_fen=fens[start],
                    recent_moves=tuple(moves[start:ply]),
                    actual_move=uci,
                )
            )
        board.push_uci(uci)
        fens.append(board.fen())
    return out


def player_episodes(
    conn: sqlite3.Connection, player: str, max_games: int | None = None
) -> Iterator[DecisionEpisode]:
    """All of a player's own decisions, in chronological game order."""
    carried: int | None = None
    for index, row in enumerate(player_games(conn, player, max_games)):
        episodes = episodes_for_game(row, index, carried)
        own = row["white_elo"] if row["color"] == "white" else row["black_elo"]
        if own is not None:
            carried = own  # only ever flows forward in time
        yield from episodes


def live_episode(board: chess.Board, self_elo: int, oppo_elo: int) -> DecisionEpisode:
    """An episode for a live position (play): same inputs as training, no label or clocks."""
    b = board.copy()
    recent: list[str] = []
    while len(recent) < HISTORY_PLIES and b.move_stack:
        recent.insert(0, b.pop().uci())
    return DecisionEpisode(
        game_id=-1,
        game_index=-1,
        ply=len(board.move_stack),
        color="white" if board.turn == chess.WHITE else "black",
        platform="live",
        played_at=None,
        time_class=None,
        tc_base_s=None,
        tc_inc_s=None,
        self_elo=self_elo,
        oppo_elo=oppo_elo,
        self_elo_source="game",
        oppo_elo_source="game",
        clock_self_s=None,
        clock_oppo_s=None,
        prev_think_s=None,
        root_fen=b.fen(),
        recent_moves=tuple(recent),
        actual_move="",
    )
