"""Headless game session: one human vs one Opponent, with full legality and termination.

Draw rule: the game ends as soon as a draw is *claimable* (threefold repetition,
50-move rule) as well as on automatic outcomes, so a sampled opponent never shuffles
indefinitely. Exported PGNs carry a `PcoGenerated` tag; the importer rejects such
games so generated play can never be mistaken for human evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import chess
import chess.pgn

from pco.play.opponent import MoveChoice, Opponent

GENERATED_TAG = "PcoGenerated"


class SessionError(ValueError):
    pass


@dataclass(frozen=True)
class GameOver:
    result: str  # '1-0' | '0-1' | '1/2-1/2'
    reason: str  # checkmate, stalemate, resignation, threefold_repetition, ...


class GameSession:
    def __init__(self, opponent: Opponent, human_color: chess.Color, start_fen: str | None = None):
        self.opponent = opponent
        self.human_color = human_color
        self.board = chess.Board(start_fen) if start_fen else chess.Board()
        self.start_fen = self.board.fen()
        self.choices: list[MoveChoice | None] = []  # per ply; None for human moves
        self.started_at = datetime.now(UTC)
        self._resigned: chess.Color | None = None

    # ------------------------------------------------------------------ state

    @property
    def over(self) -> GameOver | None:
        if self._resigned is not None:
            return GameOver("0-1" if self._resigned == chess.WHITE else "1-0", "resignation")
        outcome = self.board.outcome(claim_draw=True)
        if outcome is None:
            return None
        return GameOver(outcome.result(), outcome.termination.name.lower())

    @property
    def human_to_move(self) -> bool:
        return self.over is None and self.board.turn == self.human_color

    @property
    def opponent_to_move(self) -> bool:
        return self.over is None and self.board.turn != self.human_color

    def legal_moves(self) -> list[str]:
        return [m.uci() for m in self.board.legal_moves] if self.over is None else []

    def san_moves(self) -> list[str]:
        board = chess.Board(self.start_fen)
        out = []
        for move in self.board.move_stack:
            out.append(board.san(move))
            board.push(move)
        return out

    # ------------------------------------------------------------------ actions

    def play_human(self, uci: str) -> None:
        if not self.human_to_move:
            raise SessionError("it is not your move" if self.over is None else "the game is over")
        try:
            move = chess.Move.from_uci(uci)
        except ValueError as exc:
            raise SessionError(f"malformed move {uci!r}") from exc
        if move not in self.board.legal_moves:
            raise SessionError(f"illegal move {uci}")
        self.board.push(move)
        self.choices.append(None)

    def play_opponent(self) -> MoveChoice:
        """Ask the opponent for a move. On OpponentError the position is unchanged."""
        if not self.opponent_to_move:
            raise SessionError("it is not the opponent's move")
        choice = self.opponent.choose_move(self.board.copy())
        self.board.push(chess.Move.from_uci(choice.uci))
        self.choices.append(choice)
        return choice

    def resign(self) -> None:
        if self.over is not None:
            raise SessionError("the game is over")
        self._resigned = self.human_color

    # ------------------------------------------------------------------ export

    def to_pgn(self, human_name: str = "You") -> str:
        game = chess.pgn.Game()
        if self.start_fen != chess.STARTING_FEN:
            game.setup(chess.Board(self.start_fen))
        white, black = (
            (human_name, self.opponent.label)
            if self.human_color == chess.WHITE
            else (self.opponent.label, human_name)
        )
        game.headers.update(
            {
                "Event": "pco local game",
                "Site": "pco (local)",
                "Date": self.started_at.strftime("%Y.%m.%d"),
                "White": white,
                "Black": black,
                "Result": self.over.result if self.over else "*",
                GENERATED_TAG: "true",
            }
        )
        if self.over:
            game.headers["Termination"] = self.over.reason
        node = game
        for move, choice in zip(self.board.move_stack, self.choices, strict=True):
            node = node.add_variation(move)
            if choice is not None:
                node.comment = f"p={choice.probability:.3f}"
        return str(game) + "\n"
