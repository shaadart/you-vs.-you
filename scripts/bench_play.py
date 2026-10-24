"""Iteration 2 measurement: live move latency and memory over complete games.

Both sides are PriorOpponent(Maia-3 5M) driven through GameSession, exactly the code
path the play server uses; with --player, both sides are that player's PersonalOpponent
(prior + live engine analysis + residual + book, no caching). Games are generated for
timing only and are not stored.
Usage: uv run python scripts/bench_play.py [--games 10] [--threads 4] [--elo 1500]
       uv run python scripts/bench_play.py --home DIR --player NAME [--games 5]
"""

import argparse
import os
import random
import resource
import statistics
import time

import chess

from pco.assets import MAIA3_5M, ensure_asset
from pco.config import load_settings
from pco.play.opponent import PriorOpponent
from pco.play.session import GameSession
from pco.priors.maia3 import Maia3Prior


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--elo", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--home", help="PCO_HOME holding the profile (with --player)")
    ap.add_argument("--player", help="benchmark this player's personal opponent")
    args = ap.parse_args()

    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    t = time.perf_counter()
    settings = load_settings(args.home)
    prior = Maia3Prior(ensure_asset(settings, MAIA3_5M, offline=True), threads=args.threads)
    load_s = time.perf_counter() - t
    rng = random.Random(args.seed)
    if args.player:
        from pco.analysis.stockfish import StockfishEvaluator
        from pco.assets import stockfish_binary
        from pco.personal.opponent import PersonalOpponent
        from pco.personal.profile import PersonalProfile

        profile = PersonalProfile.latest(settings.profiles_dir, args.player)
        engines = [StockfishEvaluator(stockfish_binary(settings)) for _ in range(2)]
        white = PersonalOpponent(profile, prior, engines[0], cache=None, rng=rng)
        black = PersonalOpponent(profile, prior, engines[1], cache=None, rng=rng)
        args.elo = profile.elo
    else:
        white = PriorOpponent(prior, args.elo, rng=rng)
        black = PriorOpponent(prior, args.elo, rng=rng)

    latencies, plies, results, wall = [], [], {}, time.perf_counter()
    for _ in range(args.games):
        session = GameSession(black, chess.WHITE)  # "human" = white, driven by the prior too
        while session.over is None and len(session.board.move_stack) < 400:
            if session.human_to_move:
                choice = white.choose_move(session.board.copy())
                session.play_human(choice.uci)
            else:
                choice = session.play_opponent()
            latencies.append(choice.latency_ms)
        plies.append(len(session.board.move_stack))
        key = session.over.reason if session.over else "ply_cap"
        results[key] = results.get(key, 0) + 1
    wall = time.perf_counter() - wall

    latencies.sort()
    q = lambda p: latencies[min(len(latencies) - 1, int(len(latencies) * p))]  # noqa: E731
    print(
        f"threads={args.threads} elo={args.elo} games={args.games} load={load_s:.2f}s "
        f"loadavg={os.getloadavg()[0]:.1f}"
    )
    print(
        f"plies: total={sum(plies)} mean={statistics.mean(plies):.0f} min={min(plies)} max={max(plies)}"
    )
    print(
        f"move latency ms: p50={q(0.5):.1f} p95={q(0.95):.1f} p99={q(0.99):.1f} max={latencies[-1]:.1f}"
    )
    print(
        f"wall={wall:.1f}s  maxRSS={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f}MB "
        f"(at start {rss0:.0f}MB)"
    )
    print(f"terminations: {results}")


if __name__ == "__main__":
    main()
