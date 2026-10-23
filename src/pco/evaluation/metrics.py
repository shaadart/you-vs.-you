"""Metrics over full legal-move distributions, with breakdowns and game-level bootstrap."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from pco.decisions.analysis import EpisodeAnalysis
from pco.decisions.features import phase
from pco.evaluation.predictors import LogProbs, position_key

ECE_BINS = 10


def _check_coverage(d: LogProbs, a: EpisodeAnalysis) -> None:
    if set(d) != set(a.dist.logprobs) or not all(math.isfinite(v) for v in d.values()):
        raise ValueError(f"predictor does not cover all legal moves at {a.episode.board().fen()}")


def per_example(
    dists: Sequence[LogProbs], analyses: Sequence[EpisodeAnalysis]
) -> dict[str, np.ndarray]:
    """Arrays aligned with `analyses`: nll, top-k hit, top-1 confidence.

    Top-k hits are expected values under random tie-breaking, so a predictor with tied
    probabilities (e.g. uniform) gets no credit for ties."""
    nll, conf, hits = [], [], {1: [], 3: [], 5: []}
    for d, a in zip(dists, analyses, strict=True):
        _check_coverage(d, a)
        lp = d[a.episode.actual_move]
        nll.append(-lp)
        conf.append(math.exp(max(d.values())))
        greater = sum(v > lp for v in d.values())
        tied = sum(v == lp for v in d.values()) - 1
        for k, h in hits.items():
            h.append(
                0.0 if greater >= k else 1.0 if greater + tied < k else (k - greater) / (tied + 1)
            )
    return {
        "nll": np.array(nll),
        "top1": np.array(hits[1]),
        "top3": np.array(hits[3]),
        "top5": np.array(hits[5]),
        "conf": np.array(conf),
    }


def ece(conf: np.ndarray, hit: np.ndarray, bins: int = ECE_BINS) -> tuple[float, list[dict]]:
    """Expected calibration error of top-1 confidence, plus the reliability table."""
    edges = np.linspace(0, 1, bins + 1)
    table, total = [], 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        mask = (conf > lo) & (conf <= hi) if lo > 0 else (conf >= lo) & (conf <= hi)
        if mask.sum() == 0:
            continue
        c, h = conf[mask].mean(), hit[mask].mean()
        total += mask.sum() * abs(c - h)
        table.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(mask.sum()), "conf": c, "acc": h})
    return total / len(conf), table


def summarize(ex: dict[str, np.ndarray], mask: np.ndarray | None = None) -> dict[str, Any]:
    m = np.ones(len(ex["nll"]), bool) if mask is None else mask
    if m.sum() == 0:
        return {"n": 0}
    e, _ = ece(ex["conf"][m], ex["top1"][m])
    return {
        "n": int(m.sum()),
        "nll": float(ex["nll"][m].mean()),
        "top1": float(ex["top1"][m].mean()),
        "top3": float(ex["top3"][m].mean()),
        "top5": float(ex["top5"][m].mean()),
        "ece": float(e),
    }


def _ply_bucket(ply: int) -> str:
    return "0-9" if ply < 10 else "10-19" if ply < 20 else "20-39" if ply < 40 else "40+"


def _legal_bucket(n: int) -> str:
    return "2-10" if n <= 10 else "11-25" if n <= 25 else "26+"


def groupers(book_positions: set[str]) -> dict[str, Callable[[EpisodeAnalysis], str]]:
    """Breakdown dimensions. 'book' = position occurred in the player's training games."""
    return {
        "phase": lambda a: phase(a.episode.board()),
        "time_class": lambda a: a.episode.time_class or "unknown",
        "ply": lambda a: _ply_bucket(a.episode.ply),
        "n_legal": lambda a: _legal_bucket(len(a.dist.logprobs)),
        "book": lambda a: (
            "in_book" if position_key(a.episode.board()) in book_positions else "out_of_book"
        ),
    }


def breakdowns(
    ex: dict[str, np.ndarray], analyses: Sequence[EpisodeAnalysis], book_positions: set[str]
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    keys = groupers(book_positions)
    # temporal: earlier vs later half of the evaluated period (drift away from training)
    indices = sorted({a.episode.game_index for a in analyses})
    median = indices[len(indices) // 2] if indices else 0
    keys["test_period"] = lambda a: "early" if a.episode.game_index < median else "late"
    for dim, key in keys.items():
        labels = np.array([key(a) for a in analyses])
        out[dim] = {lab: summarize(ex, labels == lab) for lab in sorted(set(labels))}
    return out


def bootstrap_diff(
    ex: dict[str, np.ndarray],
    ref: dict[str, np.ndarray],
    game_ids: np.ndarray,
    metric: str,
    n_boot: int = 1000,
    seed: int = 0,
) -> dict[str, float]:
    """Mean of (ex - ref) with a 95% CI from resampling whole games (moves within a game
    are not independent)."""
    games, inverse = np.unique(game_ids, return_inverse=True)
    diff = ex[metric] - ref[metric]
    sums = np.bincount(inverse, weights=diff, minlength=len(games))
    counts = np.bincount(inverse, minlength=len(games)).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(games), size=(n_boot, len(games)))
    boots = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"mean": float(diff.mean()), "ci95": [float(lo), float(hi)]}
