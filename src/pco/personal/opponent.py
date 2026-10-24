"""Live personalized opponent: the same pipeline as training, applied to the game board.

Per move: prior at the player's rating -> live candidate set (prior top moves + engine
best, no actual move) -> engine scores -> residual adjustment -> opening book blend ->
sample at the profile's play temperature. The reported probability and likeness are
model estimates of how characteristic a move is, not claims about the player's intent.
"""

from __future__ import annotations

import random
import time

import chess

from pco.analysis.stockfish import Evaluator, UnsupportedPosition
from pco.decisions.analysis import (
    AnalysisConfig,
    EpisodeAnalysis,
    analyze_engine,
    prior_candidates,
    prior_distributions,
)
from pco.decisions.cache import AnalysisCache
from pco.decisions.episodes import live_episode
from pco.evaluation.predictors import book_blend, position_key
from pco.personal.features import featurize
from pco.personal.model import distribution
from pco.personal.profile import PersonalProfile
from pco.play.opponent import MoveChoice, OpponentError
from pco.priors.base import HumanPrior, MoveDistribution, PriorError, PriorRequest


class PersonalOpponent:
    def __init__(
        self,
        profile: PersonalProfile,
        prior: HumanPrior,
        evaluator: Evaluator,
        cache: AnalysisCache | None = None,
        temperature: float | None = None,
        rng: random.Random | None = None,
    ):
        if prior.model_id != profile.prior_model:
            raise OpponentError(
                f"profile was trained with prior {profile.prior_model}, loaded {prior.model_id}; "
                "retrain with `pco train`"
            )
        self.profile, self.prior, self.evaluator, self.cache = profile, prior, evaluator, cache
        self.temperature = profile.play_temperature if temperature is None else temperature
        self.rng = rng or random.Random()
        self.config = AnalysisConfig(**profile.analysis_config)
        self.w = profile.w
        self.last_degraded: str | None = None
        games = profile.data.get("games", {})
        n_games = sum(games.values()) if games else "?"
        self.label = (
            f"Personal model of {profile.player} ({n_games} games up to "
            f"{(profile.data.get('last_game') or '?')[:10]}, rating {profile.elo}, "
            f"sampling T={self.temperature:g}) - a statistical imitation, not the player"
        )

    def distribution(self, board: chess.Board) -> dict[str, float]:
        """The personal model's full legal-move distribution for `board`."""
        elo = self.profile.elo
        try:
            # Same path (and canonical precision) as training: offline features were built
            # from prior_distributions output, so live must be too (exact consistency test).
            game_dist = prior_distributions(
                self.prior, self.cache, [PriorRequest(board, elo, elo)]
            )[0]
            base = game_dist
            if self.profile.elo_offset:
                shifted = PriorRequest(board, max(0, elo + self.profile.elo_offset), elo)
                base = prior_distributions(self.prior, self.cache, [shifted])[0]
        except PriorError as exc:
            raise OpponentError(f"human prior failed: {exc}") from exc
        logprobs = base.logprobs
        self.last_degraded = None
        if board.legal_moves.count() > 1:
            cands = prior_candidates(game_dist, self.config)
            try:
                live, engine = analyze_engine(
                    self.evaluator, self.cache, board.fen(), cands, self.config
                )
            except UnsupportedPosition as exc:  # residual needs the engine: prior + book only
                self.last_degraded = f"engine unavailable ({exc}); prior + book only"
            else:
                analysis = EpisodeAnalysis(live_episode(board, elo, elo), game_dist, live, engine)
                logprobs = distribution(
                    self.w, featurize(analysis, base.logprobs), base.logprobs, self.profile.spec
                )
        return book_blend(self.profile.book, position_key(board), logprobs, self.profile.book_alpha)

    def choose_move(self, board: chess.Board) -> MoveChoice:
        t = time.perf_counter()
        logprobs = self.distribution(board)
        dist = MoveDistribution(f"personal:{self.profile.player}", logprobs)
        uci = dist.sample(self.rng, self.temperature)
        if chess.Move.from_uci(uci) not in board.legal_moves:
            raise OpponentError(f"personal model proposed illegal move {uci} in {board.fen()}")
        top = tuple(dist.top(5))
        return MoveChoice(
            uci=uci,
            probability=dist.prob(uci),
            top=top,
            source=f"personal:{self.profile.player}@{self.profile.created_at}"
            + (f" [{self.last_degraded}]" if self.last_degraded else ""),
            latency_ms=(time.perf_counter() - t) * 1000,
            likeness=dist.prob(uci) / top[0][1],
        )
