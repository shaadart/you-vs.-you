"""Maia-3 input encoding in numpy (no torch).

Ported from CSSLab/maia3 (AGPL-3.0), commit 1e13597c42d4858b7cfd7cfdae01e297263364b2:
`maia3/dataset.py` and `maia3/utils.py`. Behavior must stay identical to upstream;
`scripts/export_onnx.py` checks it against the torch reference.
"""

from __future__ import annotations

from collections.abc import Sequence

import chess
import numpy as np

_PIECE = {
    chess.PAWN: 1,
    chess.KNIGHT: 2,
    chess.BISHOP: 3,
    chess.ROOK: 4,
    chess.QUEEN: 5,
    chess.KING: 6,
}


def all_moves() -> list[str]:
    """The model's output vocabulary: every from-to square pair, then white promotions."""
    moves = [
        chess.square_name(a) + chess.square_name(b) for a in chess.SQUARES for b in chess.SQUARES
    ]
    moves.extend(
        f"{f}7{t}8{p}" for f in "abcdefgh" for t in "abcdefgh" for p in ("q", "r", "b", "n")
    )
    return moves


def mirror_move(uci: str) -> str:
    """The same move seen from the other side (black moves are encoded as white's)."""
    flip = lambda sq: sq[0] + str(9 - int(sq[1]))  # noqa: E731
    return flip(uci[:2]) + flip(uci[2:4]) + uci[4:]


def tokenize_board(board: chess.Board) -> np.ndarray:
    """(64, 12) one-hot planes, always from the side to move's point of view."""
    tokens = np.zeros((64, 12), dtype=np.float32)
    if board.turn == chess.BLACK:
        board = board.mirror()
    for square, piece in board.piece_map().items():
        tokens[square, _PIECE[piece.piece_type] - 1 + (6 if piece.color == chess.BLACK else 0)] = 1
    return tokens


def historical_tokens(boards: Sequence[np.ndarray], history: int) -> np.ndarray:
    """(64, 12*history): oldest-first planes, padded with the earliest.

    Upstream appends one more zero column (a training label) that the model slices off
    again, so it is not built here."""
    planes = list(boards)
    if len(planes) < history:
        planes = [planes[0]] * (history - len(planes)) + planes
    return np.concatenate(planes, axis=1)


def legal_mask(board: chess.Board, index: dict[str, int]) -> np.ndarray:
    mask = np.zeros(len(index), dtype=bool)
    for move in board.legal_moves:
        uci = move.uci() if board.turn == chess.WHITE else mirror_move(move.uci())
        if uci in index:
            mask[index[uci]] = True
    return mask
