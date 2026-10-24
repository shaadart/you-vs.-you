"""Maia-3 human-move prior (CSSLab, AGPL-3.0), run in-process on CPU.

Uses the upstream `maia3` package for tokenization and the model definition so the
input encoding cannot drift from what the checkpoint was trained on.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import chess
import torch
from maia3.dataset import get_historical_tokens, get_legal_moves_mask, tokenize_board
from maia3.model_registry import apply_model_config, resolve_model_spec
from maia3.models import MAIA3Model
from maia3.utils import get_all_possible_moves, mirror_move

from pco.assets import sha256_file
from pco.priors.base import MoveDistribution, PriorError, PriorRequest

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
    def __init__(self, checkpoint: Path, model_name: str = "maia3-5m", threads: int | None = None):
        if threads:
            torch.set_num_threads(threads)
        spec = resolve_model_spec(model_name)
        self.cfg = SimpleNamespace(
            history=HISTORY, use_padding=False, include_time_info=False, device="cpu"
        )
        apply_model_config(self.cfg, spec)
        # Identity = architecture + weight checksum, so caches keyed on it never mix versions.
        self.model_id = f"{spec.name}@{sha256_file(checkpoint)[:12]}"
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
        state = state.get("model_state_dict", state)
        state = {k.replace("smolgen", "gab"): v for k, v in state.items()}
        self.model = MAIA3Model(self.cfg)
        missing, unexpected = self.model.load_state_dict(state, strict=False)
        if missing or unexpected:
            raise PriorError(
                f"checkpoint does not match {spec.name}: missing={missing[:3]} "
                f"unexpected={unexpected[:3]}"
            )
        self.model.eval()
        self.vocab = get_all_possible_moves()
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
        return f"k2|{b.fen()}|{','.join(reversed(moves))}|{req.self_elo}|{req.oppo_elo}"

    def _encode(self, req: PriorRequest) -> torch.Tensor:
        tokens = deque(tokenize_board(b) for b in history_boards(req.board))
        return get_historical_tokens(tokens, self.cfg, 0.0, 0.0, 0.0, 0.0)

    @torch.no_grad()
    def predict(self, requests: Sequence[PriorRequest]) -> list[MoveDistribution]:
        if not requests:
            return []
        masks = []
        for req in requests:
            mask = get_legal_moves_mask(req.board, self.index)
            if not bool(mask.any()):
                raise PriorError(f"no legal moves in position {req.board.fen()}")
            masks.append(mask)
        tokens = torch.stack([self._encode(r) for r in requests])
        self_elo = torch.tensor([r.self_elo for r in requests], dtype=torch.long)
        oppo_elo = torch.tensor([r.oppo_elo for r in requests], dtype=torch.long)
        logits, _value, _ = self.model(tokens, self_elo, oppo_elo)
        logp = logits.float().masked_fill(~torch.stack(masks), float("-inf")).log_softmax(-1)

        out = []
        for row, req in zip(logp, requests, strict=True):
            black = req.board.turn == chess.BLACK
            dist = {}
            for move in req.board.legal_moves:
                uci = move.uci()
                dist[uci] = row[self.index[mirror_move(uci) if black else uci]].item()
            out.append(MoveDistribution(self.model_id, dist))
        return out
