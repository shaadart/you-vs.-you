"""Speed presets (the spec's hardware profiles): how much analysis each decision gets.

The personalized model is identical across presets; only the analysis budget changes:
how many prior candidates are scored and how deep Stockfish searches them. A profile
remembers the preset it was trained with (its analysis config), and live play uses the
same budget, so training and play stay consistent. Costs and fidelity: EXPERIMENTS E7.x.
"""

from __future__ import annotations

from dataclasses import dataclass

from pco.decisions.analysis import AnalysisConfig

# Training default: the most recent games carry the player's current behavior; older
# games added nothing for openings and are down-weighted by recency anyway (E7.1).
DEFAULT_RECENT_GAMES = 2000
DEFAULT_SPEED = "lite"  # equal held-out accuracy to standard/deep on 3 players (E7.2)


@dataclass(frozen=True)
class SpeedPreset:
    name: str
    analysis: AnalysisConfig
    description: str
    est_decisions_per_s: float  # cold analysis throughput, 4 workers, i5-1235U (E7.2)


PRESETS = {
    "lite": SpeedPreset(
        "lite",
        AnalysisConfig(k_max=3, mass=0.90, nodes=3_000, best_nodes=1_500),
        "default: 3 candidates, shallow engine search; matched Standard's accuracy on 3"
        " players at ~2x the speed (E7.2)",
        est_decisions_per_s=70.0,
    ),
    "standard": SpeedPreset(
        "standard",
        AnalysisConfig(),  # k_max=5, mass=0.95, nodes=10_000, best_nodes=5_000
        "5 candidates, 10k-node searches (the Iterations 3-6 setting)",
        est_decisions_per_s=45.0,
    ),
    "deep": SpeedPreset(
        "deep",
        AnalysisConfig(k_max=6, mass=0.97, nodes=30_000, best_nodes=15_000),
        "6 candidates, 30k-node searches; no measured accuracy gain, ~3x slower than Standard",
        est_decisions_per_s=30.0,
    ),
}


def preset(name: str) -> SpeedPreset:
    try:
        return PRESETS[name]
    except KeyError:
        raise ValueError(f"unknown speed preset {name!r}; choose from {sorted(PRESETS)}") from None
