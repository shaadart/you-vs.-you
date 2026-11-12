"""Export Maia-3 (maia3-5m) to ONNX and check it against the torch reference.

Needs the `export` dependency group (torch + upstream maia3):
    uv sync --group export
    uv run python scripts/export_onnx.py --checkpoint PATH.pt --out maia3-5m.onnx

The check runs both models on real positions (every ply of the test fixtures, both sides to
move, several rating pairs) and fails unless the largest absolute logit difference over legal
moves and the largest probability difference are below the limits. Prints the SHA-256 to put
into pco/assets.py.
"""

import argparse
import random
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import chess
import chess.pgn
import torch
from maia3.dataset import get_historical_tokens, get_legal_moves_mask, tokenize_board
from maia3.model_registry import apply_model_config, resolve_model_spec
from maia3.models import MAIA3Model
from maia3.utils import get_all_possible_moves

from pco.assets import MAIA3_5M, sha256_file
from pco.priors.base import PriorRequest
from pco.priors.maia3 import HISTORY, Maia3Prior, history_boards

FIXTURE_PGN = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "games.pgn"
RATINGS = [(900, 900), (1500, 1500), (2200, 1800), (1200, 2000)]
LOGIT_LIMIT = 1e-3
PROB_LIMIT = 1e-4


def _rms_norm(self, x):
    """RMSNorm spelled out: opset 17 has no rms_norm operator. Same math as torch's."""
    eps = torch.finfo(x.dtype).eps if self.eps is None else self.eps
    out = x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + eps)
    return out if self.weight is None else out * self.weight


class LogitsOnly(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, tokens, self_elo, oppo_elo):
        return self.model(tokens, self_elo, oppo_elo)[0]


def load_torch(checkpoint: Path):
    spec = resolve_model_spec("maia3-5m")
    cfg = SimpleNamespace(history=HISTORY, use_padding=False, include_time_info=False, device="cpu")
    apply_model_config(cfg, spec)
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    state = state.get("model_state_dict", state)
    state = {k.replace("smolgen", "gab"): v for k, v in state.items()}
    model = MAIA3Model(cfg)
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing or unexpected:
        raise SystemExit(f"checkpoint mismatch: missing={missing[:3]} unexpected={unexpected[:3]}")
    return model.eval(), cfg


def positions() -> list[chess.Board]:
    out = []
    with FIXTURE_PGN.open() as f:
        while (game := chess.pgn.read_game(f)) is not None:
            board = game.board()
            for move in game.mainline_moves():
                if board.legal_moves.count():
                    out.append(board.copy())
                board.push(move)
    out.append(chess.Board())
    rng = random.Random(0)  # random playouts reach odd positions: promotions, castling, checks
    for _ in range(60):
        board = chess.Board()
        for ply in range(rng.randint(10, 120)):
            moves = list(board.legal_moves)
            if not moves:
                break
            board.push(rng.choice(moves))
            if ply % 4 == 0:
                out.append(board.copy())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("maia3-5m.onnx"))
    args = ap.parse_args()

    torch.nn.RMSNorm.forward = _rms_norm  # reference and export use the same spelling
    model, cfg = load_torch(args.checkpoint)
    tokens = torch.zeros(2, 64, 12 * HISTORY)
    elo = torch.tensor([1500, 1500], dtype=torch.long)
    torch.onnx.export(
        LogitsOnly(model),
        (tokens, elo, elo),
        str(args.out),
        input_names=["tokens", "self_elo", "oppo_elo"],
        output_names=["logits"],
        dynamic_axes={k: {0: "batch"} for k in ("tokens", "self_elo", "oppo_elo", "logits")},
        opset_version=17,
        dynamo=False,
    )
    # Upstream feeds 97 columns and the model slices off the last; the graph takes the 96 used.
    prior = Maia3Prior(args.out, threads=1)
    vocab = get_all_possible_moves()
    index = {m: i for i, m in enumerate(vocab)}

    boards = positions()
    worst_logit = worst_prob = 0.0
    n = 0
    for board in boards:
        for self_elo, oppo_elo in RATINGS:
            req = PriorRequest(board=board, self_elo=self_elo, oppo_elo=oppo_elo)
            hist = deque(tokenize_board(b) for b in history_boards(board))
            ref_tokens = get_historical_tokens(hist, cfg, 0.0, 0.0, 0.0, 0.0).unsqueeze(0)
            with torch.no_grad():
                ref = model(
                    ref_tokens,
                    torch.tensor([self_elo]),
                    torch.tensor([oppo_elo]),
                )[0][0]
            mask = get_legal_moves_mask(board, index)
            got = torch.from_numpy(prior.logits([req])[0])
            worst_logit = max(worst_logit, (ref - got)[mask].abs().max().item())
            p_ref = ref.masked_fill(~mask, float("-inf")).softmax(-1)
            p_got = got.masked_fill(~mask, float("-inf")).softmax(-1)
            worst_prob = max(worst_prob, (p_ref - p_got).abs().max().item())
            n += 1

    print(f"positions x ratings checked: {n}")
    print(f"max |logit diff| over legal moves: {worst_logit:.3e} (limit {LOGIT_LIMIT:g})")
    print(f"max |prob diff|:                   {worst_prob:.3e} (limit {PROB_LIMIT:g})")
    print(f"onnx sha256: {sha256_file(args.out)}  ({args.out.stat().st_size} bytes)")
    print(f"current torch asset: {MAIA3_5M.name} {MAIA3_5M.sha256}")
    if worst_logit > LOGIT_LIMIT or worst_prob > PROB_LIMIT:
        print("FAIL: export does not match the torch reference", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
