"""The residual model as an evaluation `Predictor` (fit on train, hyperparameters on val)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from pco.decisions.analysis import EpisodeAnalysis
from pco.decisions.episodes import DecisionEpisode
from pco.evaluation.predictors import LogProbs, Predictor
from pco.personal.features import PERSONAL_FEATURE_VERSION, V1, FeatureSpec, featurize
from pco.personal.model import describe, distribution, fit_weights, mean_nll, objective, stack

LAMBDAS = (1e-1, 1e-2, 1e-3, 1e-4, 1e-5)  # strong -> weak, so each fit warm-starts the next
# Recency half-life in games, chosen on val; None = all games equal. Helped one of three
# players (the longest history) and was correctly not selected for the others (E6.1/E6.2).
HALF_LIVES: tuple[float | None, ...] = (None, 400, 200, 100, 50)

Analyze = Callable[[Sequence[DecisionEpisode]], list[EpisodeAnalysis]]


def blocks_for(analyses: Sequence[EpisodeAnalysis], base: Sequence[LogProbs]):
    """Feature blocks for non-forced decisions with engine data, plus their game index."""
    blocks, game_index = [], []
    for a, b in zip(analyses, base, strict=True):
        blk = featurize(a, b, a.episode.actual_move)
        if blk is not None:
            blocks.append(blk)
            game_index.append(a.episode.game_index)
    return blocks, np.array(game_index)


def recency_weights(game_index: np.ndarray, half_life: float | None) -> np.ndarray:
    """Weight 1 for the newest training game, halving every `half_life` games back."""
    if half_life is None or len(game_index) == 0:
        return np.ones(len(game_index))
    return 0.5 ** ((game_index.max() - game_index) / half_life)


def weight_covariance(w: np.ndarray, s, lam: float) -> np.ndarray:
    """Approximate covariance: inverse Hessian of the mean objective over the effective
    sample size (Kish, for recency weights). Moves within a game are correlated, so this is
    optimistic; consumers should demand large z (observations use |z| >= 4). The full matrix
    is kept because some weights are only meaningful as contrasts (the one-hot piece_*)."""
    _, _, hess = objective(w, s, lam)
    n_eff = s.weight.sum() ** 2 / (s.weight**2).sum()
    return np.linalg.pinv(hess) / n_eff


class Residual:
    """Player-specific residual on top of `base`, trained on the player's own decisions."""

    def __init__(
        self,
        base: Predictor,
        analyze: Analyze,
        lambdas: Sequence[float] = LAMBDAS,
        spec: FeatureSpec = V1,
        half_lives: Sequence[float | None] = HALF_LIVES,
        name: str | None = None,
    ):
        self.base, self.analyze, self.spec = base, analyze, spec
        self.lambdas, self.half_lives = tuple(lambdas), tuple(half_lives)
        self.name = name or f"residual({base.name})"
        self.w: np.ndarray | None = None
        self.lam = 0.0
        self.half_life: float | None = None
        self.val_curve: dict[str, float] = {}
        self.n_train = 0
        self.cov: np.ndarray | None = None
        self._fitted_on: tuple[int, int] | None = None

    def fit(self, train: Sequence[DecisionEpisode], val: Sequence[EpisodeAnalysis]) -> None:
        if self._fitted_on == (id(train), id(val)):  # shared by wrappers: fit once
            return
        self.base.fit(train, val)
        train_an = self.analyze(train)
        tr_blocks, tr_index = blocks_for(train_an, self.base.predict(train_an))
        va_blocks, va_index = blocks_for(val, self.base.predict(val))
        s_val = stack(va_blocks, self.spec)
        # kept for diagnostics that reuse the featurization (behavior drift)
        self.blocks = {"train": (tr_blocks, tr_index), "val": (va_blocks, va_index)}
        best = None
        self.val_curve = {}
        for h in self.half_lives:
            s_train = stack(tr_blocks, self.spec, recency_weights(tr_index, h))
            w = None
            for lam in self.lambdas:
                w = fit_weights(s_train, lam, w0=w)
                nll = mean_nll(w, s_val)
                self.val_curve[f"h={h},lam={lam:g}"] = nll
                # ties prefer no recency weighting and stronger regularization
                key = (nll, h is not None, -lam)
                if best is None or key < best[0]:
                    best = (key, w, lam, h)
        _, self.w, self.lam, self.half_life = best
        self.n_train = len(tr_blocks)
        self.cov = weight_covariance(
            self.w, stack(tr_blocks, self.spec, recency_weights(tr_index, self.half_life)), self.lam
        )
        self._fitted_on = (id(train), id(val))

    def predict(self, analyses: Sequence[EpisodeAnalysis]) -> list[LogProbs]:
        out = []
        for a, b in zip(analyses, self.base.predict(analyses), strict=True):
            blk = featurize(a, b)
            out.append(b if blk is None else distribution(self.w, blk, b, self.spec))
        return out

    def params(self) -> dict[str, Any]:
        return {
            "feature_version": PERSONAL_FEATURE_VERSION,
            "feature_groups": list(self.spec.groups),
            "lambda": self.lam,
            "half_life_games": self.half_life,
            "train_decisions": self.n_train,
            "weights": describe(self.w, self.spec) if self.w is not None else None,
            "val_nll": self.val_curve,
            "base": self.base.params(),
        }
