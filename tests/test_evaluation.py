"""Evaluation: splits, metrics, predictors, quality sweep, and the end-to-end report."""

import math
import random
from dataclasses import replace
from datetime import date, timedelta

import chess
import chess.pgn
import numpy as np
import pytest
from fakes import FakeEvaluator, FakePrior

import pco.evaluation.report as report_mod
from pco.decisions.analysis import AnalysisConfig, DecisionBuilder
from pco.decisions.cache import AnalysisCache
from pco.decisions.episodes import player_episodes
from pco.evaluation.metrics import bootstrap_diff, ece, per_example
from pco.evaluation.predictors import MAX_ELO_OFFSET, Book, EloOffset, Temperature, normalize
from pco.evaluation.quality import MoveScorer, quality_sweep
from pco.evaluation.report import EvalConfig, format_report, run_evaluation
from pco.evaluation.splits import SplitSpec, chronological_split
from pco.importers.pipeline import import_records
from pco.importers.sources import RawRecord
from pco.priors.base import MoveDistribution
from pco.storage.db import connect

OPENING = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6"]  # the player's fixed repertoire


def synthetic_pgn(i: int, moves: list[str], when: date | None) -> str:
    game = chess.pgn.Game()
    me_white = i % 2 == 0
    game.headers.update(
        {
            "White": "Me" if me_white else f"opp{i}",
            "Black": f"opp{i}" if me_white else "Me",
            "WhiteElo": "1500",
            "BlackElo": "1500",
            "TimeControl": "300+0",
            "Date": when.strftime("%Y.%m.%d") if when else "????.??.??",
            "Result": "*",
        }
    )
    node = game
    for uci in moves:
        node = node.add_variation(chess.Move.from_uci(uci))
    return str(game)


def random_continuation(rng: random.Random, plies: int) -> list[str]:
    board = chess.Board()
    moves = list(OPENING)
    for m in moves:
        board.push_uci(m)
    for _ in range(plies):
        legal = sorted(m.uci() for m in board.legal_moves)
        if not legal:
            break
        mv = rng.choice(legal)
        moves.append(mv)
        board.push_uci(mv)
    return moves


@pytest.fixture(scope="module")
def db():
    conn = connect(":memory:")
    rng = random.Random(42)
    start = date(2026, 1, 1)
    games = [random_continuation(rng, 24) for _ in range(40)]
    records = [
        RawRecord("pgn", synthetic_pgn(i, g, start + timedelta(days=i)))
        for i, g in enumerate(games)
    ]
    # a later re-import of game 4 under another date: same moves -> must not enter test
    records.append(RawRecord("pgn", synthetic_pgn(4, games[4], start + timedelta(days=60))))
    records.append(
        RawRecord("pgn", synthetic_pgn(2, random_continuation(rng, 10), None))
    )  # undated
    import_records(conn, "pgn", "synthetic", records, username="Me")
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def episodes(db):
    return list(player_episodes(db, "Me"))


# ------------------------------------------------------------------ splits


def test_split_is_chronological_game_level_and_drops_duplicates(db, episodes):
    split = chronological_split(db, episodes, SplitSpec(val_frac=0.1, test_frac=0.2))
    assert split.excluded == {"undated": 1, "duplicate_move_sequence": 1}
    dates = {k: [e.played_at for e in getattr(split, k)] for k in ("train", "val", "test")}
    assert max(dates["train"]) < min(dates["val"]) and max(dates["val"]) < min(dates["test"])
    ids = {k: set(v) for k, v in split.games.items()}
    assert not (ids["train"] & ids["val"] or ids["train"] & ids["test"] or ids["val"] & ids["test"])
    for name in ("train", "val", "test"):  # whole games only
        for gid in ids[name]:
            assert all(e.game_id in ids[name] for e in episodes if e.game_id == gid)
    assert split.fingerprint() == chronological_split(db, episodes).fingerprint()


# ------------------------------------------------------------------ metrics


class _A:  # minimal stand-in for EpisodeAnalysis in metric tests
    def __init__(self, logprobs, actual, game_id=0):
        self.dist = MoveDistribution("m", logprobs)
        self.episode = type(
            "E", (), {"actual_move": actual, "game_id": game_id, "board": lambda s: chess.Board()}
        )()


def test_topk_is_tie_aware_and_nll_uses_full_distribution():
    moves = [f"m{i}" for i in range(4)]
    uniform = {m: -math.log(4) for m in moves}
    a = _A(uniform, "m2")
    ex = per_example([uniform], [a])
    assert ex["top1"][0] == pytest.approx(0.25) and ex["top3"][0] == pytest.approx(0.75)
    assert ex["nll"][0] == pytest.approx(math.log(4))
    with pytest.raises(ValueError, match="cover"):
        per_example([{m: -math.log(3) for m in moves[:3]}], [a])  # truncated distribution


def test_ece_zero_when_calibrated_and_positive_when_overconfident():
    conf = np.array([0.8] * 10)
    assert ece(conf, np.array([1.0] * 8 + [0.0] * 2))[0] == pytest.approx(0.0)
    assert ece(conf, np.array([1.0] * 4 + [0.0] * 6))[0] == pytest.approx(0.4)


def test_bootstrap_identical_predictors_give_zero_and_ci_brackets_mean():
    rng = np.random.default_rng(1)
    ex = {"nll": rng.random(200)}
    ref = {"nll": ex["nll"] - 0.1 + rng.normal(0, 0.05, 200)}
    games = np.repeat(np.arange(20), 10)
    same = bootstrap_diff(ex, ex, games, "nll")
    assert same["mean"] == 0 and same["ci95"] == [0.0, 0.0]
    d = bootstrap_diff(ex, ref, games, "nll")
    assert d["ci95"][0] < d["mean"] < d["ci95"][1] and d["ci95"][0] > 0


# ------------------------------------------------------------------ predictors


def test_temperature_scaling_normalizes():
    d = normalize({"a": 2.0, "b": 1.0, "c": 0.0})
    for t in (0.5, 1.0, 2.0):
        s = Temperature.scale(d, t)
        assert sum(math.exp(v) for v in s.values()) == pytest.approx(1.0)
    assert Temperature.scale(d, 0.5)["a"] > d["a"]  # sharper


class EloSensitivePrior(FakePrior):
    """Puts more mass on the alphabetically first legal move as the rating rises."""

    def __init__(self):
        super().__init__("elo-sensitive@1")

    def predict(self, requests):
        self.calls += len(requests)
        out = []
        for r in requests:
            moves = sorted(m.uci() for m in r.board.legal_moves)
            q = 1 / (1 + math.exp(-(r.self_elo - 1500) / 300))
            rest = (1 - q) / max(1, len(moves) - 1)
            probs = (
                {m: (q if i == 0 else rest) for i, m in enumerate(moves)}
                if len(moves) > 1
                else {moves[0]: 1.0}
            )
            out.append(
                MoveDistribution(
                    self.model_id, {m: math.log(max(p, 1e-9)) for m, p in probs.items()}
                )
            )
        return out


def test_elo_offset_picks_validation_optimum_and_ties_prefer_zero(db, episodes):
    split = chronological_split(db, episodes)
    prior = EloSensitivePrior()
    cache = AnalysisCache(":memory:")
    b = DecisionBuilder(prior, FakeEvaluator, cache, AnalysisConfig(), workers=1)
    # relabel copies: the "player" always plays the alphabetically first move, so the
    # validation optimum is the strongest rating offset
    val = [
        replace(a, episode=replace(a.episode, actual_move=min(a.dist.logprobs)))
        for a in b.analyze(split.val)
    ]
    p = EloOffset(prior, cache)
    p.fit(split.train, val)
    # coarse -400..400, refine around +400, then keep stepping outward while it improves
    assert p.offset == MAX_ELO_OFFSET == max(p.fit_curve)
    assert min(p.fit_curve) == -400  # never searched below the coarse grid
    flat_prior = FakePrior()  # rating-insensitive: every offset gives the same NLL
    flat_val = DecisionBuilder(flat_prior, FakeEvaluator, cache, AnalysisConfig()).analyze(
        split.val
    )
    flat = EloOffset(flat_prior, cache)
    flat.fit(split.train, flat_val)
    assert flat.offset == 0
    with pytest.raises(ValueError, match="different prior"):
        EloOffset(FakePrior("other@1"), cache).fit(split.train, flat_val)


def test_book_learns_repertoire_in_book_only(db, episodes):
    split = chronological_split(db, episodes)
    b = DecisionBuilder(FakePrior(), FakeEvaluator, AnalysisCache(":memory:"), AnalysisConfig())
    val, test = b.analyze(split.val), b.analyze(split.test)
    base = report_mod.GenericPrior("fake")
    book = Book(base)
    book.fit(split.train, val)
    for a, d in zip(test, book.predict(test), strict=True):
        if a.episode.ply < len(OPENING):  # repertoire position: seen in training
            assert d[a.episode.actual_move] > a.dist.logprobs[a.episode.actual_move]
        elif a.episode.ply > 12:  # random continuation: unseen -> exactly the base
            assert d == a.dist.logprobs


# ------------------------------------------------------------------ quality


def test_quality_sweep_is_reproducible_and_argmax_is_deterministic(db, episodes):
    split = chronological_split(db, episodes)
    cache = AnalysisCache(":memory:")
    ev = FakeEvaluator()
    b = DecisionBuilder(FakePrior(), lambda: ev, cache, AnalysisConfig())
    test = b.analyze(split.test)
    dists = [a.dist.logprobs for a in test]
    s1 = quality_sweep(dists, test, MoveScorer(ev, cache, AnalysisConfig()), [0.0, 1.0], samples=3)
    s2 = quality_sweep(dists, test, MoveScorer(ev, cache, AnalysisConfig()), [0.0, 1.0], samples=3)
    assert s1 == s2
    assert 0 <= s1["separate_frac"] <= 1
    assert s1["sampled"]["0.0"]["n"] == 3 * sum(a.engine is not None for a in test)


# ------------------------------------------------------------------ end to end


def test_report_end_to_end_without_test_leakage(db, monkeypatch):
    seen_by_fit = []
    original = report_mod.make_predictors

    class Spy:
        name = "spy"

        def fit(self, train, val):
            seen_by_fit.append({e.game_id for e in train} | {a.episode.game_id for a in val})

        def predict(self, analyses):
            return [a.dist.logprobs for a in analyses]

        def params(self):
            return {}

    monkeypatch.setattr(
        report_mod, "make_predictors", lambda p, c, analyze=None: [*original(p, c, analyze), Spy()]
    )
    cfg = EvalConfig(temperatures=(0.0, 1.0), samples=1, n_boot=200)

    def run():
        return run_evaluation(db, "Me", FakePrior(), FakeEvaluator, AnalysisCache(":memory:"), cfg)

    r1, r2 = run(), run()
    split = chronological_split(db, list(player_episodes(db, "Me")))
    test_games = set(split.games["test"])
    assert seen_by_fit and not (seen_by_fit[0] & test_games)  # fit never saw test games
    preds = r1["predictors"]
    assert set(preds) == {
        "uniform",
        "maia",
        "maia+T",
        "maia+elo",
        "maia+elo+T",
        "book(maia+elo+T)",
        "residual(maia+elo)",
        "book(residual(maia+elo))",
        "spy",
    }
    assert (
        preds["book(maia+elo+T)"]["breakdowns"]["book"]["in_book"]["nll"]
        < preds["maia"]["breakdowns"]["book"]["in_book"]["nll"]
    )
    assert (
        r1["test_decisions"]["scored"] + r1["test_decisions"]["forced_excluded"]
        == r1["test_decisions"]["total"]
    )
    strip = lambda r: {k: v for k, v in r.items() if k != "timing"}  # noqa: E731
    assert strip(r1) == strip(r2)  # reproducible apart from timing
    text = format_report(r1)
    assert "dNLL vs maia" in text and "sampled-quality match" in text


def test_elo_offset_warm_start_searches_locally(db, episodes):
    split = chronological_split(db, episodes)
    prior = EloSensitivePrior()
    cache = AnalysisCache(":memory:")
    val = [
        replace(a, episode=replace(a.episode, actual_move=min(a.dist.logprobs)))
        for a in DecisionBuilder(prior, FakeEvaluator, cache, AnalysisConfig()).analyze(split.val)
    ]
    warm = EloOffset(prior, cache, start=200)
    warm.fit(split.train, val)
    assert warm.offset == MAX_ELO_OFFSET  # keeps stepping outward while it improves
    assert min(warm.fit_curve) == 100  # never searched far below the starting point
