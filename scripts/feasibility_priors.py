"""Iteration 0 feasibility benchmark: Maia-2 vs Maia-3 on CPU.

Measures top-1 / top-3 accuracy, mean actual-move NLL, single-position latency,
batched throughput and process RSS on real Lichess arena games.
Usage: uv run --group bench python scripts/feasibility_priors.py PGN [PGN ...] --model-dir DIR --only MODEL
(run one model per process so maxRSS is per model)
"""

import argparse
import math
import resource
import sys
import time
from collections import deque
from types import SimpleNamespace

import chess
import chess.pgn
import torch


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def load_positions(paths, max_games):
    """Yield dicts: board (before move), history boards (<=8, oldest first), move, elos, ply."""
    rows = []
    n_games = 0
    for path in paths:
        with open(path) as f:
            while n_games < max_games:
                game = chess.pgn.read_game(f)
                if game is None:
                    break
                h = game.headers
                if h.get("Variant", "Standard") != "Standard":
                    continue
                try:
                    we, be = int(h["WhiteElo"]), int(h["BlackElo"])
                except (KeyError, ValueError):
                    continue
                n_games += 1
                board = game.board()
                hist = deque([board.copy(stack=False)], maxlen=8)
                for ply, move in enumerate(game.mainline_moves()):
                    self_elo, oppo_elo = (we, be) if board.turn == chess.WHITE else (be, we)
                    rows.append(
                        dict(
                            fen=board.fen(),
                            hist=list(hist),
                            move=move.uci(),
                            self_elo=self_elo,
                            oppo_elo=oppo_elo,
                            ply=ply,
                            n_legal=board.legal_moves.count(),
                        )
                    )
                    board.push(move)
                    hist.append(board.copy(stack=False))
    return rows, n_games


# ---------------------------------------------------------------- Maia-2
class Maia2Prior:
    def __init__(self, kind):
        from maia2 import inference, model

        self.inference = inference
        t = time.perf_counter()
        self.model = model.from_pretrained(
            type=kind, device="cpu", save_root=ARGS.model_dir + "/maia2"
        )
        self.model.eval()
        self.load_s = time.perf_counter() - t
        self.prepared = inference.prepare()
        self.params = sum(p.numel() for p in self.model.parameters())

    def batch_logprobs(self, rows):
        from maia2.utils import mirror_move

        all_moves_dict, elo_dict, rev = self.prepared
        boards, es, eo, masks = [], [], [], []
        for r in rows:
            b, s, o, m = self.inference.preprocessing(
                r["fen"], r["self_elo"], r["oppo_elo"], elo_dict, all_moves_dict
            )
            boards.append(b)
            es.append(s)
            eo.append(o)
            masks.append(m)
        with torch.no_grad():
            logits, _, _ = self.model(torch.stack(boards), torch.tensor(es), torch.tensor(eo))
        mask = torch.stack(masks).bool()
        logp = logits.masked_fill(~mask, float("-inf")).log_softmax(-1)
        out = []
        for i, r in enumerate(rows):
            black = r["fen"].split()[1] == "b"
            mv = mirror_move(r["move"]) if black else r["move"]
            idx = all_moves_dict[mv]
            top = torch.topk(logp[i], 3).indices.tolist()
            out.append((logp[i, idx].item(), top[0] == idx, idx in top))
        return out


# ---------------------------------------------------------------- Maia-3
class Maia3Prior:
    def __init__(self, name, use_history):
        from maia3.dataset import get_historical_tokens, get_legal_moves_mask, tokenize_board
        from maia3.model_registry import (
            apply_model_config,
            resolve_checkpoint_path,
            resolve_model_spec,
        )
        from maia3.models import MAIA3Model
        from maia3.utils import get_all_possible_moves, mirror_move

        self.tok, self.hist_tok, self.mask_fn, self.mirror = (
            tokenize_board,
            get_historical_tokens,
            get_legal_moves_mask,
            mirror_move,
        )
        spec = resolve_model_spec(name)
        cfg = SimpleNamespace(history=8, use_padding=False, include_time_info=False, device="cpu")
        apply_model_config(cfg, spec)
        t = time.perf_counter()
        path = resolve_checkpoint_path(
            spec,
            checkpoint_filename=None,
            cache_dir=ARGS.model_dir + "/maia3",
            revision=None,
            local_files_only=False,
            force_download=False,
            token=None,
        )
        self.model = MAIA3Model(cfg)
        sd = torch.load(path, map_location="cpu", weights_only=True)
        sd = sd["model_state_dict"] if "model_state_dict" in sd else sd
        missing, unexpected = self.model.load_state_dict(
            {k.replace("smolgen", "gab"): v for k, v in sd.items()}, strict=False
        )
        assert not missing and not unexpected, (missing[:3], unexpected[:3])
        self.model.eval()
        self.load_s = time.perf_counter() - t
        self.cfg, self.use_history = cfg, use_history
        moves = get_all_possible_moves()
        self.mdict = {m: i for i, m in enumerate(moves)}
        self.params = sum(p.numel() for p in self.model.parameters())

    def batch_logprobs(self, rows):
        toks, masks, es, eo = [], [], [], []
        for r in rows:
            boards = r["hist"] if self.use_history else r["hist"][-1:]
            toks.append(
                self.hist_tok(deque(self.tok(b) for b in boards), self.cfg, 0.0, 0.0, 0.0, 0.0)
            )
            masks.append(self.mask_fn(chess.Board(r["fen"]), self.mdict))
            es.append(r["self_elo"])
            eo.append(r["oppo_elo"])
        with torch.no_grad():
            logits, _, _ = self.model(torch.stack(toks), torch.tensor(es), torch.tensor(eo))
        logp = logits.float().masked_fill(~torch.stack(masks), float("-inf")).log_softmax(-1)
        out = []
        for i, r in enumerate(rows):
            black = r["fen"].split()[1] == "b"
            idx = self.mdict[self.mirror(r["move"]) if black else r["move"]]
            top = torch.topk(logp[i], 3).indices.tolist()
            out.append((logp[i, idx].item(), top[0] == idx, idx in top))
        return out


def evaluate(name, prior, rows):
    res = []
    t = time.perf_counter()
    for i in range(0, len(rows), 256):
        res.extend(prior.batch_logprobs(rows[i : i + 256]))
    batch_s = time.perf_counter() - t
    # single-position latency (what live play needs)
    lat = []
    for r in rows[:200]:
        t = time.perf_counter()
        prior.batch_logprobs([r])
        lat.append(time.perf_counter() - t)
    lat.sort()

    def summarize(sel):
        sub = [res[i] for i in sel]
        n = len(sub)
        return (
            n,
            sum(x[1] for x in sub) / n,
            sum(x[2] for x in sub) / n,
            -sum(x[0] for x in sub) / n,
        )

    all_i = range(len(rows))
    mid_i = [i for i, r in enumerate(rows) if r["ply"] >= 20 and r["n_legal"] > 1]
    print(
        f"\n== {name}  params={prior.params / 1e6:.2f}M load={prior.load_s:.2f}s maxRSS={rss_mb():.0f}MB"
    )
    for label, sel in (("all", all_i), ("ply>=20,non-forced", mid_i)):
        n, a1, a3, nll = summarize(sel)
        print(f"  {label:20s} n={n:6d} top1={a1:.4f} top3={a3:.4f} nll={nll:.4f}")
    print(f"  batched throughput: {len(rows) / batch_s:.0f} pos/s (batch 256)")
    print(
        f"  single-pos latency: p50={lat[len(lat) // 2] * 1000:.1f}ms p95={lat[int(len(lat) * 0.95)] * 1000:.1f}ms"
    )
    sys.stdout.flush()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pgns", nargs="+")
    ap.add_argument("--max-games", type=int, default=10_000)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--only", default="uniform,maia2-rapid,maia2-blitz,maia3-5m,maia3-5m-nohist")
    ARGS = ap.parse_args()
    torch.set_num_threads(ARGS.threads)
    rows, n_games = load_positions(ARGS.pgns, ARGS.max_games)
    print(f"games={n_games} positions={len(rows)} torch_threads={torch.get_num_threads()}")
    only = ARGS.only.split(",")
    if "uniform" in only:
        nll = sum(math.log(r["n_legal"]) for r in rows) / len(rows)
        print(
            f"\n== uniform-legal baseline nll={nll:.4f} top1~={sum(1 / r['n_legal'] for r in rows) / len(rows):.4f}"
        )
    for key in only:
        if key == "uniform":
            continue
        if key.startswith("maia2-"):
            prior = Maia2Prior(key.split("-")[1])
        elif key == "maia3-5m":
            prior = Maia3Prior("maia3-5m", use_history=True)
        elif key == "maia3-5m-nohist":
            prior = Maia3Prior("maia3-5m", use_history=False)
        elif key == "maia3-23m":
            prior = Maia3Prior("maia3-23m", use_history=True)
        else:
            raise SystemExit(f"unknown {key}")
        evaluate(key, prior, rows)
        del prior
