"""Does sampling the prior play like real humans of that rating, or worse?

On real positions whose mover is rated in [--min-elo, --max-elo], compare the quality
of: the move the human actually played, the prior's argmax, and samples at T=1 and
T=0.5. Quality = loss vs the best candidate, all scored in one node-limited Stockfish
search (root_moves) per position. Mover's own rating conditions the prior.
Usage: uv run python scripts/strength_check.py PGN [PGN ...] [--positions 1500]
"""

import argparse
import math
import random
import statistics

import chess
import chess.pgn

from pco.analysis.stockfish import AnalysisLimit, StockfishEvaluator
from pco.assets import MAIA3_5M, ensure_asset, stockfish_binary
from pco.config import load_settings
from pco.priors.base import PriorRequest
from pco.priors.maia3 import Maia3Prior


def win_prob(cp):
    return 1 / (1 + 10 ** (-cp / 400))


def positions(paths, lo, hi, seed):
    out = []
    for path in paths:
        with open(path) as f:
            while (g := chess.pgn.read_game(f)) is not None:
                try:
                    we, be = int(g.headers["WhiteElo"]), int(g.headers["BlackElo"])
                except (KeyError, ValueError):
                    continue
                board = g.board()
                for ply, move in enumerate(g.mainline_moves()):
                    se, oe = (we, be) if board.turn == chess.WHITE else (be, we)
                    if ply >= 8 and lo <= se <= hi and board.legal_moves.count() > 1:
                        out.append((board.copy(), move.uci(), se, oe))
                    board.push(move)
    random.Random(seed).shuffle(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pgns", nargs="+")
    ap.add_argument("--positions", type=int, default=1500)
    ap.add_argument("--min-elo", type=int, default=1300)
    ap.add_argument("--max-elo", type=int, default=1700)
    ap.add_argument("--nodes", type=int, default=50_000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--temperatures", default="1,0.5", help="comma-separated sampling temperatures")
    args = ap.parse_args()
    settings = load_settings()
    prior = Maia3Prior(ensure_asset(settings, MAIA3_5M, offline=True), threads=4)
    rows = positions(args.pgns, args.min_elo, args.max_elo, args.seed)[: args.positions]
    dists = []
    for i in range(0, len(rows), 64):
        dists += prior.predict([PriorRequest(b, se, oe) for b, _, se, oe in rows[i : i + 64]])
    rng = random.Random(args.seed)
    temps = [float(t) for t in args.temperatures.split(",")]
    policies = ["human", "argmax"] + [f"sample_T{t:g}" for t in temps]
    loss = {p: [] for p in policies}
    with StockfishEvaluator(stockfish_binary(settings)) as sf:
        for (board, human, _, _), dist in zip(rows, dists, strict=True):
            picks = {"human": human, "argmax": dist.sample(rng, 0)}
            picks.update({f"sample_T{t:g}": dist.sample(rng, t) for t in temps})
            engine_best = sf.best(board, AnalysisLimit(args.nodes // 2))
            cands = sorted(set(picks.values()) | {engine_best.uci})
            evals = {e.uci: e.cp for e in sf.evaluate(board, cands, AnalysisLimit(args.nodes))}
            top = max(evals.values())
            for p, mv in picks.items():
                loss[p].append(
                    (max(0, min(1000, top - evals[mv])), win_prob(top) - win_prob(evals[mv]))
                )
    n = len(rows)
    # Likelihood view: NLL of the human's actual move under temperature-scaled policies.
    print("NLL of actual move by temperature:", end="")
    for t in [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2]:
        nll = 0.0
        for (_, human, _, _), d in zip(rows, dists, strict=True):
            scaled = {m: lp / t for m, lp in d.logprobs.items()}
            peak = max(scaled.values())
            log_z = peak + math.log(sum(math.exp(v - peak) for v in scaled.values()))
            nll -= scaled[human] - log_z
        print(f" T{t:g}={nll / n:.4f}", end="")
    print()
    print(f"positions={n} mover elo {args.min_elo}-{args.max_elo}, ply>=8, nodes={args.nodes}")
    print(
        f"{'policy':12s} {'mean cp loss':>12s} {'mistake>=100':>13s} {'blunder>=300':>13s} {'mean wp loss':>13s}"
    )
    for p in policies:
        cps = [c for c, _ in loss[p]]
        wps = [w for _, w in loss[p]]
        print(
            f"{p:12s} {statistics.mean(cps):12.1f} {sum(c >= 100 for c in cps) / n:13.3f} "
            f"{sum(c >= 300 for c in cps) / n:13.3f} {statistics.mean(wps):13.4f}"
        )


if __name__ == "__main__":
    main()
