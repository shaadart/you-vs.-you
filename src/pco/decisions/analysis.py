"""Derived analysis for decisions: prior distribution, candidate set, engine evaluations.

`analyze_engine` is the single code path for both offline dataset building and live
inference, so candidate sets and their evaluations are computed identically in both.
The actual move is never part of the live candidate set: when it falls outside, it is
evaluated in its own separate search and flagged, so its presence cannot change the
other candidates' scores.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import chess

from pco.analysis.stockfish import AnalysisLimit, CandidateEval, Evaluator, UnsupportedPosition
from pco.decisions.cache import AnalysisCache, cache_key, canonical_logprobs
from pco.decisions.episodes import DecisionEpisode
from pco.priors.base import HumanPrior, MoveDistribution, PriorRequest

PRIOR_BATCH = 64  # fastest offline batch size on CPU (EXPERIMENTS E0.2)


@dataclass(frozen=True)
class AnalysisConfig:
    k_max: int = 5  # most prior candidates considered
    mass: float = (
        0.95  # stop adding prior candidates once their cumulative probability reaches this
    )
    nodes: int = 10_000  # node budget for scoring the candidate set (E0.3)
    best_nodes: int = 5_000  # node budget for the unrestricted engine-best search


@dataclass(frozen=True)
class EngineAnalysis:
    best: CandidateEval  # unrestricted search result (engine's own preferred move)
    evals: dict[str, CandidateEval]  # live candidate set, scored in one shared search
    actual_eval: CandidateEval | None  # actual move scored separately iff outside the live set


@dataclass(frozen=True)
class EpisodeAnalysis:
    episode: DecisionEpisode
    dist: MoveDistribution
    live_candidates: tuple[str, ...]  # what live inference would consider
    engine: EngineAnalysis | None  # None for forced positions or when the engine failed
    engine_error: str | None = None  # why engine analysis is missing (not cached)

    @property
    def forced(self) -> bool:
        return len(self.dist.logprobs) == 1

    @property
    def actual_in_live(self) -> bool:
        return self.episode.actual_move in self.live_candidates


def prior_candidates(dist: MoveDistribution, config: AnalysisConfig) -> list[str]:
    """Adaptive: few candidates when the prior is confident, up to k_max when it is not."""
    out, mass = [], 0.0
    for uci, p in dist.top(config.k_max):
        out.append(uci)
        mass += p
        if mass >= config.mass and len(out) >= min(2, len(dist.logprobs)):
            break
    return out


def cached_search(
    evaluator: Evaluator,
    cache: AnalysisCache | None,
    fen: str,
    nodes: int,
    cands: Sequence[str],
) -> list[CandidateEval]:
    """One cached engine search: unrestricted best move if `cands` is empty, otherwise a
    shared MultiPV search over exactly `cands`. Key = engine identity + inputs."""
    mode = "set" if cands else "best"
    key = cache_key(evaluator.engine_id, mode, nodes, fen, ",".join(sorted(cands)))
    hit = cache.get_engine(key) if cache else None
    if hit is not None:
        return hit
    board = chess.Board(fen)
    if cands:
        result = evaluator.evaluate(board, cands, AnalysisLimit(nodes))
    else:
        result = [evaluator.best(board, AnalysisLimit(nodes))]
    if cache:
        cache.put_engine(key, evaluator.engine_id, nodes, result)
    return result


def analyze_engine(
    evaluator: Evaluator,
    cache: AnalysisCache | None,
    fen: str,
    prior_cands: Sequence[str],
    config: AnalysisConfig,
    actual: str | None = None,
) -> tuple[tuple[str, ...], EngineAnalysis]:
    """Engine-best search, then one shared search over prior candidates + engine best.

    Evaluations use the bare FEN (repetition-blind) so the cache key fully determines
    the result. `actual` (training only) is scored separately when outside the set.
    """
    best = cached_search(evaluator, cache, fen, config.best_nodes, ())[0]
    live = tuple(dict.fromkeys([*prior_cands, best.uci]))  # ordered, de-duplicated
    evals = cached_search(evaluator, cache, fen, config.nodes, live)
    actual_eval = None
    if actual is not None and actual not in live:
        actual_eval = cached_search(evaluator, cache, fen, config.nodes, (actual,))[0]
    return live, EngineAnalysis(best, {e.uci: e for e in evals}, actual_eval)


def prior_distributions(
    prior: HumanPrior, cache: AnalysisCache | None, requests: Sequence[PriorRequest]
) -> list[MoveDistribution]:
    """Batched prior inference with caching keyed by model id + exact model inputs."""
    out: list[MoveDistribution | None] = [None] * len(requests)
    misses = []
    for i, req in enumerate(requests):
        key = cache_key(prior.model_id, prior.input_key(req))
        hit = cache.get_prior(key) if cache else None
        if hit is not None:
            out[i] = MoveDistribution(prior.model_id, hit)
        else:
            misses.append((i, key))
    for start in range(0, len(misses), PRIOR_BATCH):
        chunk = misses[start : start + PRIOR_BATCH]
        for (i, key), dist in zip(
            chunk, prior.predict([requests[i] for i, _ in chunk]), strict=True
        ):
            out[i] = MoveDistribution(dist.model_id, canonical_logprobs(dist.logprobs))
            if cache:
                cache.put_prior(key, prior.model_id, dist.logprobs)
    return out  # type: ignore[return-value]


class DecisionBuilder:
    """Analyze many episodes: prior in batches, engine work across a pool of evaluators.

    Resumable and interruptible: results are cached and committed per chunk, so a rerun
    after Ctrl-C only computes what is missing.
    """

    def __init__(
        self,
        prior: HumanPrior,
        make_evaluator: Callable[[], Evaluator],
        cache: AnalysisCache,
        config: AnalysisConfig | None = None,
        workers: int = 1,
        chunk: int = 512,
    ):
        self.prior, self.cache, self.chunk = prior, cache, chunk
        self.config = config or AnalysisConfig()
        self.evaluators = [make_evaluator() for _ in range(max(1, workers))]

    def close(self) -> None:
        for ev in self.evaluators:
            close = getattr(ev, "close", None)
            if close:
                close()

    def _analyze_one(
        self, ep: DecisionEpisode, board: chess.Board, dist: MoveDistribution, evaluator: Evaluator
    ) -> EpisodeAnalysis:
        cands = prior_candidates(dist, self.config)
        if board.legal_moves.count() == 1:  # forced: no choice, no engine work
            return EpisodeAnalysis(ep, dist, tuple(cands), None)
        try:
            live, engine = analyze_engine(
                evaluator, self.cache, board.fen(), cands, self.config, ep.actual_move
            )
        except UnsupportedPosition as exc:  # one odd position must not abort a whole build
            return EpisodeAnalysis(ep, dist, tuple(cands), None, engine_error=str(exc))
        return EpisodeAnalysis(ep, dist, live, engine)

    def _analyze_lane(self, items: list, evaluator: Evaluator) -> list[EpisodeAnalysis]:
        return [self._analyze_one(ep, board, dist, evaluator) for ep, board, dist in items]

    def _prior_items(self, part: Sequence[DecisionEpisode]) -> list:
        boards = [ep.board() for ep in part]
        requests = [
            PriorRequest(b, e.self_elo, e.oppo_elo) for b, e in zip(boards, part, strict=True)
        ]
        dists = prior_distributions(self.prior, self.cache, requests)
        return list(zip(part, boards, dists, strict=True))

    def analyze(
        self,
        episodes: Sequence[DecisionEpisode],
        progress: bool | Callable[[int, int, float], None] = False,
    ) -> list[EpisodeAnalysis]:
        """Pipelined: while the engine pool scores chunk n, the prior runs on chunk n+1.

        `progress`: True prints to stderr; a callable receives (done, total, rate/s)."""
        results: list[EpisodeAnalysis] = []
        n_workers = len(self.evaluators)
        chunks = [episodes[i : i + self.chunk] for i in range(0, len(episodes), self.chunk)]
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            items = self._prior_items(chunks[0]) if chunks else []
            for n in range(len(chunks)):
                # Lane w gets items w, w+n, ... and evaluator w exclusively: an evaluator
                # (one engine process) is never used by two threads at once.
                futures = [
                    pool.submit(self._analyze_lane, items[w::n_workers], self.evaluators[w])
                    for w in range(n_workers)
                ]
                next_items = self._prior_items(chunks[n + 1]) if n + 1 < len(chunks) else []
                lane_results = [f.result() for f in futures]
                results.extend(
                    lane_results[i % n_workers][i // n_workers] for i in range(len(items))
                )
                self.cache.commit()
                items = next_items
                if callable(progress):
                    done = len(results)
                    progress(done, len(episodes), done / max(1e-9, time.perf_counter() - t0))
                elif progress:
                    done = len(results)
                    rate = done / (time.perf_counter() - t0)
                    eta = (len(episodes) - done) / rate if rate else 0
                    hits = self.cache.hits
                    print(
                        f"  {done}/{len(episodes)} decisions  {rate:.0f}/s  "
                        f"ETA {eta / 60:.1f} min  cache hits: prior {hits['prior']}, "
                        f"engine {hits['engine']}",
                        file=sys.stderr,
                        flush=True,
                    )
        return results
