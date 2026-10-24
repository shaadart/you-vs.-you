"""Iteration 7 experiment: should the opening book use the player's whole history?

The book needs only moves (no engine/prior analysis), so a full-history book is cheap.
Holdout = the newest 10% of the `--recent` window (exactly the profile's validation
games). Book alpha is chosen on the older half of the holdout games and reported on the
newer half; base = Maia at the val-fitted Elo offset. Variants: counts from the recent
training window only, from the whole history, and recency-weighted whole history.
Usage: uv run python scripts/book_history.py --home H PLAYER [--recent 2000]
"""

import argparse
from collections import Counter

import numpy as np

from pco.analysis.stockfish import StockfishEvaluator
from pco.assets import MAIA3_5M, ensure_asset, stockfish_binary
from pco.config import load_settings
from pco.decisions.analysis import AnalysisConfig, DecisionBuilder
from pco.decisions.cache import AnalysisCache
from pco.decisions.episodes import player_episodes
from pco.evaluation.metrics import bootstrap_diff, per_example
from pco.evaluation.predictors import EloOffset, book_blend, position_key
from pco.evaluation.splits import SplitSpec, chronological_split
from pco.priors.maia3 import Maia3Prior
from pco.storage.db import connect

ALPHAS = (0.5, 1, 2, 4, 8, 16, 32)


def weighted_counts(episodes, half_life, newest):
    counts: dict[str, Counter] = {}
    for ep in episodes:
        w = 1.0 if half_life is None else 0.5 ** ((newest - ep.game_index) / half_life)
        counts.setdefault(position_key(ep.board()), Counter())[ep.actual_move] += w
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("player")
    ap.add_argument("--home", required=True)
    ap.add_argument("--recent", type=int, default=2000)
    args = ap.parse_args()
    s = load_settings(args.home)
    conn = connect(s.db_path)
    episodes = [e for e in player_episodes(conn, args.player) if e.played_at]
    dated = sorted({e.game_index for e in episodes})
    window = set(dated[-args.recent :])
    older = [e for e in episodes if e.game_index not in window]
    recent = [e for e in episodes if e.game_index in window]
    split = chronological_split(conn, recent, SplitSpec(val_frac=0.1, test_frac=0.0))
    prior = Maia3Prior(ensure_asset(s, MAIA3_5M, offline=True), threads=4)
    cache = AnalysisCache(s.analysis_cache_path)
    b = DecisionBuilder(
        prior, lambda: StockfishEvaluator(stockfish_binary(s)), cache, AnalysisConfig(), 2
    )
    val = [a for a in b.analyze(split.val) if a.engine is not None]
    b.close()
    base = EloOffset(prior, cache)
    base.fit(split.train, val)
    base_d = base.predict(val)
    newest = max(dated)
    holdout_games = sorted({a.episode.game_index for a in val})
    cut = holdout_games[len(holdout_games) // 2]
    tune = np.array([a.episode.game_index < cut for a in val])
    games = np.array([a.episode.game_id for a in val])
    variants = {
        "recent window (current)": weighted_counts(split.train, None, newest),
        "whole history": weighted_counts([*older, *split.train], None, newest),
        "whole history, half-life 1000": weighted_counts([*older, *split.train], 1000, newest),
        "whole history, half-life 400": weighted_counts([*older, *split.train], 400, newest),
    }
    print(
        f"{args.player}: older games {len({e.game_index for e in older})}, recent window "
        f"{len(window)} (train {len(split.games['train'])}, holdout {len(split.games['val'])}); "
        f"elo offset {base.offset:+d}; holdout decisions tune/report {tune.sum()}/{(~tune).sum()}"
    )
    results = {}
    for name, counts in variants.items():

        def dists(alpha, counts=counts):
            return [
                book_blend(counts, position_key(a.episode.board()), d, alpha)
                for a, d in zip(val, base_d, strict=True)
            ]

        nll_tune = {
            al: -np.mean(
                [
                    d[a.episode.actual_move]
                    for d, a, t in zip(dists(al), val, tune, strict=True)
                    if t
                ]
            )
            for al in ALPHAS
        }
        alpha = min(nll_tune, key=nll_tune.get)
        ex = per_example(dists(alpha), val)
        rep = {k: v[~tune] for k, v in ex.items()}
        in_book = np.array([position_key(a.episode.board()) in counts for a in val])[~tune]
        results[name] = (rep, alpha, in_book)
    ref = results["recent window (current)"][0]
    base_ex = {k: v[~tune] for k, v in per_example(base_d, val).items()}
    print(
        f"{'book variant':32s} {'alpha':>5s} {'in-book':>7s} {'NLL':>7s} {'top1':>6s}  {'dNLL vs current [95% CI]':>27s}"
    )
    print(
        f"{'(no book: maia+elo)':32s} {'-':>5s} {'-':>7s} {base_ex['nll'].mean():7.4f} {base_ex['top1'].mean():6.3f}"
    )
    for name, (rep, alpha, in_book) in results.items():
        d = bootstrap_diff(rep, ref, games[~tune], "nll")
        print(
            f"{name:32s} {alpha:5g} {in_book.mean():7.3f} {rep['nll'].mean():7.4f} {rep['top1'].mean():6.3f}  "
            f"{d['mean']:+.4f} [{d['ci95'][0]:+.4f},{d['ci95'][1]:+.4f}]"
        )


if __name__ == "__main__":
    main()
