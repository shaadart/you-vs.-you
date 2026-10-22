"""Human-move prior interface.

A prior maps (position + recent history, rating context) to a probability
distribution over *all* legal moves. Implementations are swappable (Maia-3 today);
personalization layers consume the log-probabilities, never the model internals.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import chess


class PriorError(RuntimeError):
    pass


@dataclass(frozen=True)
class PriorRequest:
    """One decision point. `board` should carry its move stack when history exists:
    priors that use history read up to their own limit from it."""

    board: chess.Board
    self_elo: int
    oppo_elo: int


@dataclass(frozen=True)
class MoveDistribution:
    model_id: str
    logprobs: dict[str, float]  # UCI move -> log P; covers every legal move, sums to 1

    def prob(self, uci: str) -> float:
        lp = self.logprobs.get(uci)
        return 0.0 if lp is None else math.exp(lp)

    def top(self, k: int) -> list[tuple[str, float]]:
        ranked = sorted(self.logprobs.items(), key=lambda kv: kv[1], reverse=True)
        return [(m, math.exp(lp)) for m, lp in ranked[:k]]

    def sample(self, rng: random.Random, temperature: float = 1.0) -> str:
        """Sample a move; temperature 0 means argmax (deterministic)."""
        if temperature <= 0:
            return max(self.logprobs, key=self.logprobs.__getitem__)
        moves = list(self.logprobs)
        scaled = [self.logprobs[m] / temperature for m in moves]
        peak = max(scaled)
        weights = [math.exp(s - peak) for s in scaled]
        return rng.choices(moves, weights=weights, k=1)[0]


class HumanPrior(Protocol):
    model_id: str

    def predict(self, requests: Sequence[PriorRequest]) -> list[MoveDistribution]:
        """Batched inference. Raises PriorError for positions without legal moves."""
        ...
