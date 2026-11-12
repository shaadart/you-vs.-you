"""Maia-3 human-move prior (CSSLab, AGPL-3.0), run in-process on CPU with onnxruntime.

The weights are the released `maia3-5m` checkpoint exported to ONNX by
`scripts/export_onnx.py`, which also checks the export against the torch reference.
The input encoding is vendored in `maia3_tokens` so it cannot drift from the checkpoint.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import chess
import numpy as np
import onnxruntime as ort

from pco.assets import sha256_file
from pco.priors.base import MoveDistribution, PriorError, PriorRequest
from pco.priors.maia3_tokens import (
    all_moves,
    historical_tokens,
    legal_mask,
    mirror_move,
    tokenize_board,
)

HISTORY = 8  # positions per input, fixed by the released checkpoints


def history_boards(board: chess.Board, limit: int = HISTORY) -> list[chess.Board]:
    """The current position plus up to limit-1 predecessors from the move stack, oldest first."""
    b = board.copy()
    out = [b.copy(stack=False)]
    while len(out) < limit and b.move_stack:
        b.pop()
        out.append(b.copy(stack=False))
    out.reverse()
    return out


class Maia3Prior:
    def __init__(self, model: Path, model_name: str = "maia3-5m", threads: int | None = None):
        opts = ort.SessionOptions()
        if threads:
            opts.intra_op_num_threads = threads
            opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        try:
            self.session = ort.InferenceSession(
                str(model), opts, providers=["CPUExecutionProvider"]
            )
        except Exception as exc:  # corrupt or wrong file: say which one
            raise PriorError(f"cannot load {model}: {exc}") from exc
        # Identity = architecture + weight checksum, so caches keyed on it never mix versions.
        self.model_id = f"{model_name}-onnx@{sha256_file(model)[:12]}"
        self.vocab = all_moves()
        self.index = {m: i for i, m in enumerate(self.vocab)}

    def input_key(self, req: PriorRequest) -> str:
        """Everything this model's output depends on: the history window and ratings.

        The window's oldest position plus the moves since determine all 8 positions
        exactly, at the cost of one FEN instead of eight (E6.1: FEN generation dominated
        cached training time)."""
        b = req.board.copy(stack=HISTORY - 1)
        moves = []
        while len(moves) < HISTORY - 1 and b.move_stack:
            moves.append(b.pop().uci())
        return f"k3|{b.fen()}|{','.join(reversed(moves))}|{req.self_elo}|{req.oppo_elo}"

    def _encode(self, req: PriorRequest) -> np.ndarray:
        return historical_tokens([tokenize_board(b) for b in history_boards(req.board)], HISTORY)

    def logits(self, requests: Sequence[PriorRequest]) -> np.ndarray:
        """Raw (batch, 4352) move logits, before masking."""
        tokens = np.stack([self._encode(r) for r in requests]).astype(np.float32)
        self_elo = np.array([r.self_elo for r in requests], dtype=np.int64)
        oppo_elo = np.array([r.oppo_elo for r in requests], dtype=np.int64)
        (out,) = self.session.run(
            ["logits"], {"tokens": tokens, "self_elo": self_elo, "oppo_elo": oppo_elo}
        )
        return out

    def predict(self, requests: Sequence[PriorRequest]) -> list[MoveDistribution]:
        if not requests:
            return []
        masks = []
        for req in requests:
            mask = legal_mask(req.board, self.index)
            if not mask.any():
                raise PriorError(f"no legal moves in position {req.board.fen()}")
            masks.append(mask)
        logits = np.where(np.stack(masks), self.logits(requests).astype(np.float64), -np.inf)
        peak = logits.max(axis=-1, keepdims=True)
        logp = logits - peak - np.log(np.exp(logits - peak).sum(axis=-1, keepdims=True))

        out = []
        for row, req in zip(logp, requests, strict=True):
            black = req.board.turn == chess.BLACK
            dist = {}
            for move in req.board.legal_moves:
                uci = move.uci()
                dist[uci] = float(row[self.index[mirror_move(uci) if black else uci]])
            out.append(MoveDistribution(self.model_id, dist))
        return out
