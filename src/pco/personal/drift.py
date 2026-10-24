"""Basic behavior-change detection (reported, never acted on automatically).

Question: do the player's recent decisions follow different tendencies than their older
ones? Fit the residual separately on the older and the newer half of the training games,
then score both on the most recent (validation) games. If the newer-half model predicts
the recent games better, with a game-clustered 95% CI excluding 0, behavior has
measurably shifted. The largest weight differences say along which tendencies.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from pco.evaluation.metrics import bootstrap_diff
from pco.personal.features import Block, FeatureSpec
from pco.personal.model import Stacked, _log_normalizer, describe, fit_weights, stack

MIN_VAL_GAMES = 30


def per_decision_nll(w: np.ndarray, s: Stacked) -> np.ndarray:
    logits = s.base_lp + s.x @ w
    log_z = _log_normalizer(s, logits)
    inside = s.label_row >= 0
    chosen = np.where(inside, logits[np.maximum(s.label_row, 0)], s.outside_lp)
    return log_z - chosen


def behavior_drift(
    train_blocks: Sequence[Block],
    train_index: np.ndarray,
    val_blocks: Sequence[Block],
    val_games: np.ndarray,
    spec: FeatureSpec,
    lam: float,
    top: int = 5,
) -> dict[str, Any]:
    # a game-clustered bootstrap over few games is anti-conservative: require enough games
    if len(train_blocks) < 200 or len(np.unique(val_games)) < MIN_VAL_GAMES:
        return {"status": "insufficient data"}
    cut = np.median(train_index)
    early = [b for b, i in zip(train_blocks, train_index, strict=True) if i < cut]
    late = [b for b, i in zip(train_blocks, train_index, strict=True) if i >= cut]
    w_early = fit_weights(stack(early, spec), lam)
    w_late = fit_weights(stack(late, spec), lam)
    s_val = stack(val_blocks, spec)
    gain = bootstrap_diff(
        {"nll": per_decision_nll(w_early, s_val)},
        {"nll": per_decision_nll(w_late, s_val)},
        val_games,
        "nll",
    )  # > 0: the newer-half model predicts the newest games better
    changes = dict(zip(spec.names, (w_late - w_early).round(4).tolist(), strict=True))
    largest = sorted(changes.items(), key=lambda kv: -abs(kv[1]))[:top]
    detected = gain["ci95"][0] > 0
    return {
        "status": "change detected" if detected else "no clear change",
        "recent_model_nll_gain": gain,  # mean NLL(early model) - NLL(late model) on val
        "largest_weight_changes": dict(largest),
        "early_weights": describe(w_early, spec),
        "late_weights": describe(w_late, spec),
    }
