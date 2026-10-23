"""Move predictors for evaluation: each yields a full distribution over legal moves.

Every predictor covers *every* legal move with nonzero probability, so the actual
move's log-likelihood is always defined (no candidate-set truncation). Fitting uses
only the train and validation splits; the test split is never seen by `fit`.

Baselines compose: Book(Temperature(EloOffset(prior))) is one predictor.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from typing import Any, Protocol

import chess

from pco.decisions.analysis import EpisodeAnalysis, prior_distributions
from pco.decisions.cache import AnalysisCache
from pco.decisions.episodes import DecisionEpisode
from pco.priors.base import HumanPrior, PriorRequest

LogProbs = dict[str, float]


def normalize(scores: dict[str, float]) -> LogProbs:
    peak = max(scores.values())
    log_z = peak + math.log(sum(math.exp(v - peak) for v in scores.values()))
    return {m: v - log_z for m, v in scores.items()}


def mean_nll(dists: Sequence[LogProbs], analyses: Sequence[EpisodeAnalysis]) -> float:
    return -sum(d[a.episode.actual_move] for d, a in zip(dists, analyses, strict=True)) / len(dists)


def position_key(board: chess.Board) -> str:
    """Position identity for opening familiarity: placement, side, castling, en passant."""
    return board.epd()


class Predictor(Protocol):
    name: str

    def fit(self, train: Sequence[DecisionEpisode], val: Sequence[EpisodeAnalysis]) -> None: ...

    def predict(self, analyses: Sequence[EpisodeAnalysis]) -> list[LogProbs]: ...

    def params(self) -> dict[str, Any]: ...


class Uniform:
    name = "uniform"

    def fit(self, train, val) -> None:
        pass

    def predict(self, analyses):
        out = []
        for a in analyses:
            moves = list(a.dist.logprobs)
            out.append({m: -math.log(len(moves)) for m in moves})
        return out

    def params(self):
        return {}


class GenericPrior:
    """The human prior conditioned on the ratings recorded for the game."""

    def __init__(self, model_id: str):
        self.name = "maia"
        self.model_id = model_id

    def fit(self, train, val) -> None:
        pass

    def predict(self, analyses):
        return [a.dist.logprobs for a in analyses]

    def params(self):
        return {"model": self.model_id}


class EloOffset:
    """Prior with the player's rating shifted by one constant, chosen on validation NLL.

    Captures 'plays like a stronger/weaker player than their rating' and platform rating
    scale differences (Maia-3 is trained on Lichess ratings).
    """

    def __init__(self, prior: HumanPrior, cache: AnalysisCache | None, grid: Sequence[int] = ()):
        self.prior, self.cache = prior, cache
        self.grid = list(grid) or list(range(-600, 601, 100))
        self.offset = 0
        self.fit_curve: dict[int, float] = {}
        self.name = "maia+elo"

    def _dists(self, analyses: Sequence[EpisodeAnalysis], offset: int) -> list[LogProbs]:
        if any(a.dist.model_id != self.prior.model_id for a in analyses):
            raise ValueError("analyses were built with a different prior than this predictor's")
        if offset == 0:
            return [a.dist.logprobs for a in analyses]
        reqs = [
            PriorRequest(a.episode.board(), max(0, a.episode.self_elo + offset), a.episode.oppo_elo)
            for a in analyses
        ]
        return [d.logprobs for d in prior_distributions(self.prior, self.cache, reqs)]

    def fit(self, train, val) -> None:
        self.fit_curve = {o: mean_nll(self._dists(val, o), val) for o in self.grid}
        # ties (e.g. an Elo-insensitive prior) prefer no change
        self.offset = min(self.fit_curve, key=lambda o: (self.fit_curve[o], abs(o)))

    def predict(self, analyses):
        return self._dists(analyses, self.offset)

    def params(self):
        return {"elo_offset": self.offset, "val_nll_by_offset": self.fit_curve}


class Temperature:
    """Base distribution sharpened/flattened by a temperature chosen on validation NLL."""

    def __init__(self, base: Predictor, grid: Sequence[float] = ()):
        self.base = base
        self.grid = list(grid) or [0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2, 1.4]
        self.t = 1.0
        self.name = f"{base.name}+T"

    @staticmethod
    def scale(d: LogProbs, t: float) -> LogProbs:
        return normalize({m: lp / t for m, lp in d.items()})

    def fit(self, train, val) -> None:
        self.base.fit(train, val)
        base = self.base.predict(val)
        self.t = min(
            self.grid,
            key=lambda t: (mean_nll([self.scale(d, t) for d in base], val), abs(t - 1.0)),
        )

    def predict(self, analyses):
        return [self.scale(d, self.t) for d in self.base.predict(analyses)]

    def params(self):
        return {"temperature": self.t, "base": self.base.params()}


class Book:
    """Opening familiarity: the player's own move counts in positions seen in training.

    P(m | s) = (count(s, m) + alpha * P_base(m | s)) / (count(s) + alpha). Positions never
    seen in training fall back to the base exactly, so gains here measure memorized
    repertoire, not general play (report in-book and out-of-book separately).
    """

    def __init__(self, base: Predictor, grid: Sequence[float] = ()):
        self.base = base
        self.grid = list(grid) or [0.25, 0.5, 1, 2, 4, 8, 16, 32]
        self.alpha = 4.0
        self.counts: dict[str, Counter] = {}
        self.name = f"book({base.name})"

    @staticmethod
    def build_counts(train: Sequence[DecisionEpisode]) -> dict[str, Counter]:
        counts: dict[str, Counter] = {}
        for ep in train:
            counts.setdefault(position_key(ep.board()), Counter())[ep.actual_move] += 1
        return counts

    def _blend(self, a: EpisodeAnalysis, base: LogProbs, alpha: float) -> LogProbs:
        seen = self.counts.get(position_key(a.episode.board()))
        if not seen:
            return base
        total = sum(seen.values())
        return {
            m: math.log((seen.get(m, 0) + alpha * math.exp(lp)) / (total + alpha))
            for m, lp in base.items()
        }

    def fit(self, train, val) -> None:
        self.base.fit(train, val)
        self.counts = self.build_counts(train)
        base = self.base.predict(val)
        self.alpha = min(
            self.grid,
            key=lambda al: mean_nll(
                [self._blend(a, d, al) for a, d in zip(val, base, strict=True)], val
            ),
        )

    def predict(self, analyses):
        base = self.base.predict(analyses)
        return [self._blend(a, d, self.alpha) for a, d in zip(analyses, base, strict=True)]

    def params(self):
        return {"alpha": self.alpha, "book_positions": len(self.counts), "base": self.base.params()}
