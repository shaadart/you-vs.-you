"""Sampled-quality match: does sampling a predictor play like the player? (E2.3)

For held-out non-forced positions, compare the engine loss of the player's actual
moves with the loss of moves *sampled* from a predictor at several temperatures.
NLL can be optimal at T~1 while samples are far noisier than the player, so this is
reported alongside likelihood, and the play temperature is chosen from it.

Losses use the same reference as the features: the best evaluation in the live set's
shared search. Moves outside the live set (and not the cached actual move) are scored
by a separate cached single-move search; their share is reported as `separate_frac`.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Callable, Sequence
from typing import Any

from pco.analysis.stockfish import Evaluator
from pco.decisions.analysis import AnalysisConfig, EpisodeAnalysis, cached_search
from pco.decisions.cache import AnalysisCache
from pco.decisions.features import clip_cp, win_prob
from pco.evaluation.predictors import LogProbs
from pco.priors.base import MoveDistribution

MISTAKE_CP, BLUNDER_CP = 100, 300


class MoveScorer:
    def __init__(self, evaluator: Evaluator, cache: AnalysisCache | None, config: AnalysisConfig):
        self.evaluator, self.cache, self.config = evaluator, cache, config
        self.lookups = self.separate = 0

    def loss(self, a: EpisodeAnalysis, uci: str) -> tuple[int, float]:
        """(cp loss, win-probability loss) of `uci`, mover's perspective, both >= 0."""
        engine = a.engine
        top = clip_cp(max(e.cp for e in engine.evals.values()))
        self.lookups += 1
        ev = engine.evals.get(uci)
        if ev is None and engine.actual_eval is not None and engine.actual_eval.uci == uci:
            ev = engine.actual_eval
        if ev is None:
            self.separate += 1
            fen = a.episode.board().fen()
            ev = cached_search(self.evaluator, self.cache, fen, self.config.nodes, (uci,))[0]
        cp = clip_cp(ev.cp)
        return max(0, top - cp), max(0.0, win_prob(top) - win_prob(cp))


def stats(losses: Sequence[tuple[int, float]]) -> dict[str, float]:
    cps = [c for c, _ in losses]
    return {
        "n": len(losses),
        "mean_cp_loss": statistics.mean(cps),
        "mistake_rate": sum(c >= MISTAKE_CP for c in cps) / len(cps),
        "blunder_rate": sum(c >= BLUNDER_CP for c in cps) / len(cps),
        "mean_wp_loss": statistics.mean(w for _, w in losses),
    }


def quality_sweep(
    dists: Sequence[LogProbs],
    analyses: Sequence[EpisodeAnalysis],
    scorer: MoveScorer,
    temperatures: Sequence[float],
    samples: int = 2,
    seed: int = 0,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Actual-move quality vs sampled quality per temperature (non-forced positions only).
    `progress(done, total)` is called after each temperature."""
    pairs = [(d, a) for d, a in zip(dists, analyses, strict=True) if a.engine is not None]
    actual = stats([scorer.loss(a, a.episode.actual_move) for _, a in pairs])
    before = scorer.separate
    out: dict[str, Any] = {"actual": actual, "sampled": {}}
    for t in temperatures:
        losses = []
        for i, (d, a) in enumerate(pairs):
            dist = MoveDistribution("eval", d)
            for s in range(samples):
                rng = random.Random(seed * 1_000_003 + i * 101 + s)  # reproducible per draw
                losses.append(scorer.loss(a, dist.sample(rng, t)))
        st = stats(losses)
        st["cp_loss_gap"] = st["mean_cp_loss"] - actual["mean_cp_loss"]
        st["blunder_gap"] = st["blunder_rate"] - actual["blunder_rate"]
        out["sampled"][str(t)] = st
        if progress:
            progress(len(out["sampled"]), len(temperatures))
    lookups = len(pairs) * samples * len(temperatures)
    out["separate_frac"] = (scorer.separate - before) / lookups if lookups else 0.0
    return out


def quality_matching_temperature(sweep: dict[str, Any]) -> float:
    """Temperature whose samples' mean cp loss is closest to the player's actual moves."""
    return float(min(sweep["sampled"], key=lambda t: abs(sweep["sampled"][t]["cp_loss_gap"])))
