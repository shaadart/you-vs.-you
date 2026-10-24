"""Personal profile: the trained, versioned artifact behind a personalized opponent.

Training uses all of a player's dated games: the oldest 90% fit the residual weights
and the book; the newest 10% choose regularization, Elo offset, book alpha and the
play temperature (sampled-quality match, E2.3/E4). The artifact is self-contained
JSON under $PCO_HOME/profiles/<player>/, reproducible from the games + pinned models.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from pco import __version__
from pco.analysis.stockfish import Evaluator
from pco.decisions.analysis import AnalysisConfig, DecisionBuilder
from pco.decisions.cache import AnalysisCache
from pco.decisions.episodes import player_episodes
from pco.evaluation.metrics import per_example, summarize
from pco.evaluation.predictors import Book, EloOffset, GenericPrior
from pco.evaluation.quality import MoveScorer, quality_matching_temperature, quality_sweep
from pco.evaluation.splits import SplitSpec, chronological_split
from pco.personal.drift import behavior_drift
from pco.personal.features import PERSONAL_FEATURE_VERSION, FeatureSpec
from pco.personal.predictor import HALF_LIVES, Residual
from pco.priors.base import HumanPrior

PROFILE_FORMAT = 2  # 2: feature groups + recency half-life
# v1 features: context groups (time control, clock, opponent, phase) did not generalize
# across players in a pre-registered test (EXPERIMENTS E6.2); they stay opt-in.
DEFAULT_SPEC = FeatureSpec()
TEMPERATURES = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


@dataclass
class PersonalProfile:
    player: str
    created_at: str
    pco_version: str
    profile_format: int
    feature_version: int
    feature_groups: list[str]
    feature_names: list[str]
    prior_model: str
    engine_id: str
    analysis_config: dict[str, Any]
    elo: int  # the player's most recent rating: conditions the prior in play
    elo_offset: int
    weights: list[float]
    reg_lambda: float
    half_life_games: float | None
    book_alpha: float
    book: dict[str, dict[str, int]]  # position key -> the player's move counts
    play_temperature: float
    data: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)

    @property
    def w(self) -> np.ndarray:
        return np.array(self.weights)

    @property
    def spec(self) -> FeatureSpec:
        return FeatureSpec(tuple(self.feature_groups))

    def save(self, root: Path) -> Path:
        directory = root / self.player
        directory.mkdir(parents=True, exist_ok=True)
        stamp = self.created_at.replace(":", "").replace("-", "")
        path = directory / f"profile-{stamp}.json"
        path.write_text(json.dumps(asdict(self)))
        return path

    @classmethod
    def load(cls, path: Path) -> PersonalProfile:
        data = json.loads(path.read_text())
        if data.get("profile_format") != PROFILE_FORMAT:
            raise ValueError(f"{path}: unsupported profile format {data.get('profile_format')}")
        stale = (
            data.get("feature_version") != PERSONAL_FEATURE_VERSION
            or data.get("feature_names") != FeatureSpec(tuple(data.get("feature_groups", ()))).names
        )
        if stale:
            raise ValueError(f"{path}: built with other features; retrain with `pco train`")
        return cls(**data)

    @classmethod
    def latest(cls, root: Path, player: str) -> PersonalProfile:
        files = sorted((root / player).glob("profile-*.json"))
        if not files:
            raise FileNotFoundError(f"no profile for {player!r}; run `pco train {player}`")
        return cls.load(files[-1])


def train_profile(
    conn,
    player: str,
    prior: HumanPrior,
    make_evaluator: Callable[[], Evaluator],
    cache: AnalysisCache,
    config: AnalysisConfig | None = None,
    workers: int = 1,
    progress: bool = False,
    spec: FeatureSpec | None = None,
    half_lives: tuple[float | None, ...] = HALF_LIVES,
    previous: PersonalProfile | None = None,
    recent_games: int | None = None,
) -> PersonalProfile:
    """Train a profile; `previous` (the player's last profile) warm-starts the Elo search.

    `recent_games` keeps only the player's N most recent dated games (fast training on
    current behavior); None uses the whole history."""
    config = config or AnalysisConfig()
    spec = spec or DEFAULT_SPEC
    t0 = time.perf_counter()
    episodes = list(player_episodes(conn, player))
    if recent_games is not None:
        dated = sorted({e.game_index for e in episodes if e.played_at})
        keep = set(dated[-recent_games:])
        episodes = [e for e in episodes if e.game_index in keep]
    split = chronological_split(conn, episodes, SplitSpec(val_frac=0.1, test_frac=0.0))
    if len(split.games["train"]) < 20 or not split.val:
        raise ValueError(f"{player!r} needs at least ~25 dated games to train a profile")
    builder = DecisionBuilder(prior, make_evaluator, cache, config, workers)
    try:
        train = builder.analyze(split.train, progress)
        val = builder.analyze(split.val, progress)
        start = previous.elo_offset if previous is not None else None
        residual = Residual(
            EloOffset(prior, cache, start=start),
            analyze=lambda _eps: train,
            spec=spec,
            half_lives=half_lives,
        )
        model = Book(residual)
        model.fit(split.train, val)
        choice = [a for a in val if a.engine is not None]
        scorer = MoveScorer(builder.evaluators[0], cache, config)
        sweep = quality_sweep(model.predict(choice), choice, scorer, TEMPERATURES, samples=2)
        play_t = quality_matching_temperature(sweep)
        (tr_blocks, tr_index), (va_blocks, va_index) = (
            residual.blocks["train"],
            residual.blocks["val"],
        )
        drift = behavior_drift(tr_blocks, tr_index, va_blocks, va_index, spec, residual.lam)
        val_model = summarize(per_example(model.predict(choice), choice))
        val_maia = summarize(per_example(GenericPrior(prior.model_id).predict(choice), choice))
        engine_id = builder.evaluators[0].engine_id
    finally:
        builder.close()
        cache.commit()

    # Deployed book: all dated games (train + val); alpha was chosen with train-only counts.
    book = Book.build_counts([*split.train, *split.val])
    last = max((e for e in episodes if e.played_at), key=lambda e: (e.game_index, e.ply))
    return PersonalProfile(
        player=player,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        pco_version=__version__,
        profile_format=PROFILE_FORMAT,
        feature_version=PERSONAL_FEATURE_VERSION,
        feature_groups=list(spec.groups),
        feature_names=spec.names,
        prior_model=prior.model_id,
        engine_id=engine_id,
        analysis_config=asdict(config),
        elo=last.self_elo,
        elo_offset=residual.base.offset,
        weights=[float(v) for v in residual.w],
        reg_lambda=residual.lam,
        half_life_games=residual.half_life,
        book_alpha=model.alpha,
        book={k: dict(Counter(v)) for k, v in book.items()},
        play_temperature=play_t,
        data={
            "split_fingerprint": split.fingerprint(),
            "games": {k: len(v) for k, v in split.games.items()},
            "decisions": {"train": len(split.train), "val": len(split.val)},
            "first_game": split.boundaries.get("train"),
            "last_game": last.played_at,
            "train_seconds": round(time.perf_counter() - t0, 1),
            # incremental training: only uncached work costs anything
            "cache": {"hits": dict(cache.hits), "computed": dict(cache.misses)},
        },
        validation={
            "model": val_model,
            "generic_maia": val_maia,
            "quality_sweep": sweep,
            "behavior_drift": drift,
        },
    )
