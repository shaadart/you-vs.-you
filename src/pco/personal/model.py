"""Residual conditional logit: a player-specific adjustment of the human prior.

    logit(m) = log P_base(m) + w . phi(m, s)    for m in the live candidate set C
    logit(m) = log P_base(m)                    for every other legal move
    P(m) = exp(logit(m)) / Z

w = 0 reproduces the base distribution exactly; L2 regularization pulls toward it, so
with little evidence the model stays the generic prior. The objective (mean NLL + L2)
is convex in w; Newton's method with backtracking solves it in a few iterations.
A training decision whose actual move lies outside C still counts: its likelihood is
P_base(actual) / Z, so the model learns how much mass to keep outside C.
Decisions can carry weights (recency): the objective is then the weighted mean NLL.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from pco.personal.features import V1, Block, FeatureSpec


@dataclass
class Stacked:
    x: np.ndarray  # (n_cands, d)
    base_lp: np.ndarray  # (n_cands,)
    starts: np.ndarray  # (n_eps,) first candidate row of each decision
    ep_of: np.ndarray  # (n_cands,) decision index of each row
    rest_lp: np.ndarray  # (n_eps,)
    label_row: np.ndarray  # (n_eps,) stacked row of the actual move, -1 if outside C
    outside_lp: np.ndarray  # (n_eps,) base log-prob of the actual move when outside C
    weight: np.ndarray  # (n_eps,) decision weights, mean 1


def stack(
    blocks: Sequence[Block], spec: FeatureSpec = V1, weights: np.ndarray | None = None
) -> Stacked:
    sizes = np.array([len(b.moves) for b in blocks])
    starts = np.concatenate([[0], np.cumsum(sizes)[:-1]]).astype(int)
    label_row = np.array(
        [s + b.label if b.label >= 0 else -1 for s, b in zip(starts, blocks, strict=True)]
    )
    ep_of = np.repeat(np.arange(len(blocks)), sizes)
    ctx = np.vstack([b.context for b in blocks])
    w = np.ones(len(blocks)) if weights is None else np.asarray(weights, float)
    return Stacked(
        x=spec.expand(np.vstack([b.x for b in blocks]), ctx[ep_of]),
        base_lp=np.concatenate([b.base_lp for b in blocks]),
        starts=starts,
        ep_of=ep_of,
        rest_lp=np.array([b.rest_lp for b in blocks]),
        label_row=label_row,
        outside_lp=np.array([b.actual_base_lp for b in blocks]),
        weight=w / w.mean(),
    )


def _log_normalizer(s: Stacked, logits: np.ndarray) -> np.ndarray:
    peak = np.maximum(np.maximum.reduceat(logits, s.starts), s.rest_lp)
    total = np.add.reduceat(np.exp(logits - peak[s.ep_of]), s.starts)
    total = total + np.exp(s.rest_lp - peak)  # exp(-inf) == 0 when C covers every move
    return peak + np.log(total)


def objective(w: np.ndarray, s: Stacked, lam: float) -> tuple[float, np.ndarray, np.ndarray]:
    """Weighted mean NLL + lam/2 |w|^2, its gradient and Hessian."""
    logits = s.base_lp + s.x @ w
    log_z = _log_normalizer(s, logits)
    inside = s.label_row >= 0
    chosen = np.where(inside, logits[np.maximum(s.label_row, 0)], s.outside_lp)
    n = s.weight.sum()
    nll = float((s.weight * (log_z - chosen)).sum() / n)

    p = np.exp(logits - log_z[s.ep_of])  # candidate probabilities
    mu = np.add.reduceat(p[:, None] * s.x, s.starts)  # (n_eps, d) expected features
    observed = np.zeros_like(w)
    if inside.any():
        observed = (s.weight[inside][:, None] * s.x[s.label_row[inside]]).sum(axis=0)
    grad = ((s.weight[:, None] * mu).sum(axis=0) - observed) / n + lam * w
    pw = p * s.weight[s.ep_of]
    hess = ((s.x * pw[:, None]).T @ s.x - (mu * s.weight[:, None]).T @ mu) / n
    return nll + 0.5 * lam * float(w @ w), grad, hess + lam * np.eye(len(w))


def fit_weights(
    s: Stacked,
    lam: float,
    max_iter: int = 50,
    tol: float = 1e-7,
    w0: np.ndarray | None = None,
) -> np.ndarray:
    """Newton with backtracking; `w0` warm-starts a fit (e.g. along a lambda grid)."""
    w = np.zeros(s.x.shape[1]) if w0 is None else np.array(w0, float)
    f, g, h = objective(w, s, lam)
    for _ in range(max_iter):
        step = np.linalg.solve(h, g)
        t = 1.0
        while True:  # backtracking (Armijo)
            w_new = w - t * step
            f_new, g_new, h_new = objective(w_new, s, lam)
            if f_new <= f - 1e-4 * t * float(g @ step) or t < 1e-6:
                break
            t *= 0.5
        converged = abs(f - f_new) < tol
        w, f, g, h = w_new, f_new, g_new, h_new
        if converged or np.linalg.norm(g) < tol:
            break
    return w


def mean_nll(w: np.ndarray, s: Stacked) -> float:
    """Unweighted mean NLL (evaluation is never recency-weighted)."""
    flat = Stacked(**{**s.__dict__, "weight": np.ones(len(s.starts))})
    return objective(w, flat, 0.0)[0]


def distribution(
    w: np.ndarray, block: Block, base: dict[str, float], spec: FeatureSpec = V1
) -> dict[str, float]:
    """Full legal-move log-probabilities under the residual model."""
    logits = dict(base)
    x = spec.expand(block.x, np.repeat(block.context[None], len(block.moves), axis=0))
    adjusted = block.base_lp + x @ w
    for m, v in zip(block.moves, adjusted, strict=True):
        logits[m] = float(v)
    peak = max(logits.values())
    log_z = peak + math.log(sum(math.exp(v - peak) for v in logits.values()))
    return {m: v - log_z for m, v in logits.items()}


def describe(w: np.ndarray, spec: FeatureSpec = V1) -> dict[str, float]:
    return {name: round(float(v), 4) for name, v in zip(spec.names, w, strict=True)}
