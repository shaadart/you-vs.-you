"""Opponents choose moves for a live game. The UI only ever talks to this protocol.

There is deliberately no silent fallback: if the model fails, `OpponentError` is
raised and surfaced to the player, never replaced by some other move source.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Protocol

import chess

from pco.priors.base import HumanPrior, PriorError, PriorRequest


class OpponentError(RuntimeError):
    pass


@dataclass(frozen=True)
class MoveChoice:
    uci: str
    probability: float  # probability of the chosen move under the opponent's distribution
    top: tuple[tuple[str, float], ...]  # most likely moves, for display
    source: str  # model id that produced the distribution
    latency_ms: float
    # P(chosen) / P(most likely): how characteristic the move is under the model (1 = its
    # top choice). A model estimate of player-likeness, not a statement of intent.
    likeness: float | None = None


class Opponent(Protocol):
    label: str  # human-readable description shown to the player

    def choose_move(self, board: chess.Board) -> MoveChoice: ...


# Provisional play default (EXPERIMENTS E2.3): sampling Maia-3 at T=1 blunders ~40-50%
# more often than real humans of the same rating, although T~0.9-1 is likelihood-optimal.
# T~0.6-0.7 matched human move quality. To be fitted per player in Iterations 4-5.
DEFAULT_PLAY_TEMPERATURE = 0.7


class PriorOpponent:
    """Samples from a generic human-move prior at a fixed rating. Not personalized."""

    def __init__(
        self,
        prior: HumanPrior,
        elo: int,
        opponent_elo: int | None = None,
        temperature: float = DEFAULT_PLAY_TEMPERATURE,
        rng: random.Random | None = None,
    ):
        self.prior = prior
        self.elo = elo
        self.opponent_elo = opponent_elo or elo
        self.temperature = temperature
        self.rng = rng or random.Random()
        self.label = (
            f"Generic human prior {prior.model_id} at Elo {elo} "
            f"(temperature {temperature:g}) - not personalized"
        )

    def choose_move(self, board: chess.Board) -> MoveChoice:
        t = time.perf_counter()
        try:
            # The board carries its move stack: the prior reads its own history from it.
            dist = self.prior.predict([PriorRequest(board, self.elo, self.opponent_elo)])[0]
        except PriorError as exc:
            raise OpponentError(f"human prior failed: {exc}") from exc
        uci = dist.sample(self.rng, self.temperature)
        if chess.Move.from_uci(uci) not in board.legal_moves:  # defensive: contract check
            raise OpponentError(f"prior proposed illegal move {uci} in {board.fen()}")
        return MoveChoice(
            uci=uci,
            probability=dist.prob(uci),
            top=tuple(dist.top(5)),
            source=dist.model_id,
            latency_ms=(time.perf_counter() - t) * 1000,
            likeness=dist.prob(uci) / dist.top(1)[0][1],
        )
