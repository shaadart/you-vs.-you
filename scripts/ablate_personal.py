"""Iteration 6 ablation: which context groups / recency weighting help the residual?

Same chronological split as `pco eval` (70/10/20). Features are computed once; each
variant chooses its own lambda (and half-life) on validation; test is used only to
report paired, game-clustered bootstrap differences vs the v1 residual (all decisions
and out-of-book). Base = Maia at the val-fitted Elo offset, as in `pco eval`.
Usage: uv run python scripts/ablate_personal.py --home DIR PLAYER [--workers 4]
"""

import argparse
import json
import time

import numpy as np

from pco.analysis.stockfish import StockfishEvaluator
from pco.assets import MAIA3_5M, ensure_asset, stockfish_binary
from pco.config import load_settings
from pco.decisions.analysis import AnalysisConfig, DecisionBuilder
from pco.decisions.cache import AnalysisCache
from pco.decisions.episodes import player_episodes
from pco.evaluation.metrics import bootstrap_diff, per_example
from pco.evaluation.predictors import Book, EloOffset, position_key
from pco.evaluation.splits import chronological_split
from pco.personal.features import FeatureSpec
from pco.personal.model import distribution, fit_weights, mean_nll, stack
from pco.personal.predictor import LAMBDAS, blocks_for, recency_weights
from pco.priors.maia3 import Maia3Prior
from pco.storage.db import connect

ALL = ("time_control", "clock", "opponent", "phase")
VARIANTS = {
    "v1": ((), (None,)),
    "+time_control": (("time_control",), (None,)),
    "+clock": (("clock",), (None,)),
    "+opponent": (("opponent",), (None,)),
    "+phase": (("phase",), (None,)),
    "+all": (ALL, (None,)),
    "v1+recency": ((), (None, 400, 200, 100, 50, 25)),
    "+phase+recency": (("phase",), (None, 400, 200, 100, 50, 25)),
    "+all+recency": (ALL, (None, 400, 200, 100, 50, 25)),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("player")
    ap.add_argument("--home", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out")
    args = ap.parse_args()
    s = load_settings(args.home)
    conn = connect(s.db_path)
    split = chronological_split(conn, list(player_episodes(conn, args.player)))
    prior = Maia3Prior(ensure_asset(s, MAIA3_5M, offline=True), threads=4)
    cache = AnalysisCache(s.analysis_cache_path)
    sf = stockfish_binary(s)
    b = DecisionBuilder(
        prior, lambda: StockfishEvaluator(sf), cache, AnalysisConfig(), args.workers
    )
    t = time.perf_counter()
    train, val, test = (b.analyze(x) for x in (split.train, split.val, split.test))
    b.close()
    base = EloOffset(prior, cache)
    base.fit(split.train, val)
    tr_blocks, tr_idx = blocks_for(train, base.predict(train))
    va_blocks, _ = blocks_for(val, base.predict(val))
    test_choice = [a for a in test if a.engine is not None]
    te_base = base.predict(test_choice)
    te_blocks, _ = blocks_for(test_choice, te_base)
    prep = time.perf_counter() - t
    book = Book.build_counts(split.train)
    oob = np.array([position_key(a.episode.board()) not in book for a in test_choice])
    games = np.array([a.episode.game_id for a in test_choice])
    print(
        f"{args.player}: split {split.fingerprint()}  train/val/test decisions "
        f"{len(tr_blocks)}/{len(va_blocks)}/{len(te_blocks)}  prep {prep:.0f}s  elo offset {base.offset:+d}"
    )

    results, examples = {}, {}
    for name, (groups, half_lives) in VARIANTS.items():
        spec = FeatureSpec(groups)
        t = time.perf_counter()
        s_val = stack(va_blocks, spec)
        best = None
        for h in half_lives:
            s_tr = stack(tr_blocks, spec, recency_weights(tr_idx, h))
            w = None
            for lam in LAMBDAS:
                w = fit_weights(s_tr, lam, w0=w)
                key = (mean_nll(w, s_val), h is not None, -lam)
                if best is None or key < best[0]:
                    best = (key, w, lam, h)
        (val_nll, _, _), w, lam, h = best
        fit_s = time.perf_counter() - t
        dists = [distribution(w, blk, bd, spec) for blk, bd in zip(te_blocks, te_base, strict=True)]
        ex = per_example(dists, test_choice)
        examples[name] = ex
        results[name] = {
            "val_nll": val_nll,
            "lambda": lam,
            "half_life": h,
            "n_features": len(spec.names),
            "fit_s": fit_s,
            "test_nll": float(ex["nll"].mean()),
            "test_top1": float(ex["top1"].mean()),
            "test_oob_nll": float(ex["nll"][oob].mean()),
        }
    ref = examples["v1"]
    print(
        f"{'variant':16s} {'d':>3s} {'lam':>6s} {'h':>5s} {'val NLL':>8s} {'test NLL':>9s} {'top1':>6s}"
        f"  {'dNLL vs v1 [95% CI]':>27s}  {'out-of-book dNLL vs v1':>27s}  fit"
    )
    for name, r in results.items():
        ex = examples[name]
        d_all = bootstrap_diff(ex, ref, games, "nll")
        d_oob = bootstrap_diff(
            {k: v[oob] for k, v in ex.items()},
            {k: v[oob] for k, v in ref.items()},
            games[oob],
            "nll",
        )
        r["vs_v1"], r["vs_v1_oob"] = d_all, d_oob
        fmt = lambda d: f"{d['mean']:+.4f} [{d['ci95'][0]:+.4f},{d['ci95'][1]:+.4f}]"  # noqa: E731
        print(
            f"{name:16s} {r['n_features']:3d} {r['lambda']:6g} {str(r['half_life']):>5s} {r['val_nll']:8.4f} "
            f"{r['test_nll']:9.4f} {r['test_top1']:6.3f}  {fmt(d_all):>27s}  {fmt(d_oob):>27s}  {r['fit_s']:.1f}s"
        )
    if args.out:
        with open(args.out, "w") as f:
            json.dump(
                {"player": args.player, "split": split.fingerprint(), "results": results},
                f,
                indent=1,
            )


if __name__ == "__main__":
    main()
