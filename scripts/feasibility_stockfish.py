"""Iteration 0 feasibility: cost and fidelity of node-limited Stockfish candidate scoring.

Candidates are the prior's top-K moves (the realistic Iteration 3 workload). Each budget
is compared against a 200k-node reference: mean |cp error| (clipped to +/-1000) and
agreement on the best candidate.
Usage: uv run python scripts/feasibility_stockfish.py PGN [PGN ...] [--positions 200]
"""

import argparse
import random
import statistics
import time

import chess
import chess.pgn

from pco.analysis.stockfish import AnalysisLimit, StockfishEvaluator
from pco.assets import MAIA3_5M, ensure_asset, stockfish_binary
from pco.config import load_settings
from pco.priors.base import PriorRequest
from pco.priors.maia3 import Maia3Prior


def sample_positions(paths, n, seed=0):
    boards = []
    for path in paths:
        with open(path) as f:
            while (game := chess.pgn.read_game(f)) is not None:
                if game.headers.get("Variant", "Standard") != "Standard":
                    continue
                board = game.board()
                for ply, move in enumerate(game.mainline_moves()):
                    if ply >= 10 and board.legal_moves.count() >= 8:
                        boards.append(board.copy())
                    board.push(move)
    random.Random(seed).shuffle(boards)
    return boards[:n]


def clip(cp):
    return max(-1000, min(1000, cp))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pgns", nargs="+")
    ap.add_argument("--positions", type=int, default=200)
    args = ap.parse_args()
    settings = load_settings()
    prior = Maia3Prior(ensure_asset(settings, MAIA3_5M, offline=True), threads=4)
    boards = sample_positions(args.pgns, args.positions)
    dists = prior.predict([PriorRequest(b, 1500, 1500) for b in boards])
    print(f"positions={len(boards)} (ply>=10, >=8 legal moves)")

    with StockfishEvaluator(stockfish_binary(settings), hash_mb=64) as sf:  # as run for E0.3
        print(f"engine={sf.engine_id} threads=1 hash=64MB")
        for k in (3, 5, 8):
            cands = [[m for m, _ in d.top(k)] for d in dists]
            ref = [
                {e.uci: e for e in sf.evaluate(b, c, AnalysisLimit(200_000))}
                for b, c in zip(boards, cands, strict=True)
            ]
            for nodes in (2_000, 10_000, 50_000):
                times, errs, agree = [], [], 0
                for b, c, r in zip(boards, cands, ref, strict=True):
                    t = time.perf_counter()
                    evals = sf.evaluate(b, c, AnalysisLimit(nodes))
                    times.append(time.perf_counter() - t)
                    errs += [abs(clip(e.cp) - clip(r[e.uci].cp)) for e in evals]
                    agree += evals[0].uci == max(r.values(), key=lambda e: e.cp).uci
                times.sort()
                print(
                    f"K={k} nodes={nodes:>6}: p50={times[len(times) // 2] * 1000:6.1f}ms "
                    f"p95={times[int(len(times) * 0.95)] * 1000:6.1f}ms "
                    f"mean|cp err|={statistics.mean(errs):5.1f} best-agree={agree / len(boards):.2f}"
                )


if __name__ == "__main__":
    main()
