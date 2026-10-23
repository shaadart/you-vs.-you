"""Decision episodes, analysis caching, candidate rules, features, leakage protection."""

import json
import math
from pathlib import Path

import chess
import pytest
from fakes import FakeEvaluator, FakePrior

from pco.analysis.stockfish import AnalysisLimit
from pco.decisions.analysis import (
    AnalysisConfig,
    DecisionBuilder,
    analyze_engine,
    prior_candidates,
)
from pco.decisions.cache import AnalysisCache
from pco.decisions.episodes import HISTORY_PLIES, episodes_for_game, player_episodes
from pco.decisions.features import FEATURE_VERSION, episode_record
from pco.importers.pipeline import import_records
from pco.importers.sources import pgn_records
from pco.priors.base import MoveDistribution
from pco.storage.db import connect

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn():
    c = connect(":memory:")
    path = FIXTURES / "games.pgn"
    import_records(c, "pgn", str(path), pgn_records(path), username="Alice")
    yield c
    c.close()


def game_row(conn, external_id):
    return conn.execute("SELECT * FROM player_games WHERE external_id=?", (external_id,)).fetchone()


# ------------------------------------------------------------------ episodes


def test_episodes_cover_only_the_players_own_moves(conn):
    row = game_row(conn, "live/222222222")  # Alice is white, 12 plies
    eps = episodes_for_game(row, 0, None)
    assert [e.ply for e in eps] == [0, 2, 4, 6, 8, 10]
    moves = row["moves_uci"].split()
    replay = chess.Board(row["start_fen"])
    for ply, uci in enumerate(moves):
        if ply % 2 == 0:
            ep = next(e for e in eps if e.ply == ply)
            assert ep.board().fen() == replay.fen()  # exact position before the move
            assert ep.actual_move == uci
            assert len(ep.recent_moves) == min(ply, HISTORY_PLIES)
        replay.push_uci(uci)


def test_clock_context_uses_only_pre_move_clocks(conn):
    row = game_row(conn, "live/222222222")
    clocks = json.loads(row["clocks_s"])
    eps = {e.ply: e for e in episodes_for_game(row, 0, None)}
    assert eps[0].clock_self_s == 180.0 and eps[0].clock_oppo_s == 180.0  # initial base
    assert eps[4].clock_self_s == clocks[2] and eps[4].clock_oppo_s == clocks[3]
    assert eps[4].prev_think_s == pytest.approx(clocks[0] - clocks[2] + 2)  # +2 s increment
    assert eps[0].prev_think_s is None


def test_no_leakage_from_future_moves_result_or_post_move_clock(conn):
    row = dict(game_row(conn, "live/222222222"))
    full = {e.ply: e for e in episodes_for_game(row, 0, None)}
    p = 6
    moves = row["moves_uci"].split()
    clocks = json.loads(row["clocks_s"])
    truncated = dict(
        row,
        moves_uci=" ".join(moves[: p + 1]),
        clocks_s=json.dumps(clocks[:p] + [0.1]),  # post-move clock of ply p tampered
        result="1-0",  # the real game was 0-1
        termination="tampered",
    )
    cut = {e.ply: e for e in episodes_for_game(truncated, 0, None)}
    assert set(cut) == {0, 2, 4, 6}
    for ply in cut:
        assert cut[ply] == full[ply]


def test_missing_ratings_are_carried_forward_only(conn):
    eps = list(player_episodes(conn, "Alice"))
    by_game = {}
    for e in eps:
        by_game.setdefault(e.game_index, e)
    order = [(e.played_at, e.self_elo_source, e.self_elo) for e in by_game.values()]
    # chronological: endgame 2025-01-05 (no elo, nothing earlier) -> default; daily 1300;
    # chess.com 1450; undated OTB game sorts last and carries the most recent rating.
    assert order == [
        ("2025-01-05", "default", 1500),
        ("2026-08-30T10:00:00Z", "game", 1300),
        ("2026-09-20T08:15:42Z", "game", 1450),
        (None, "carried", 1450),
    ]


def test_missing_clocks_are_none_not_guessed(conn):
    row = game_row(conn, "daily/987654321")
    assert all(
        e.clock_self_s is None and e.prev_think_s is None for e in episodes_for_game(row, 0, None)
    )


# ------------------------------------------------------------------ candidates & analysis


def test_prior_candidates_are_adaptive():
    confident = MoveDistribution(
        "m", {"a": math.log(0.97), "b": math.log(0.02), "c": math.log(0.01)}
    )
    spread = MoveDistribution("m", {m: math.log(1 / 10) for m in "abcdefghij"})
    cfg = AnalysisConfig(k_max=5, mass=0.95)
    assert prior_candidates(confident, cfg) == ["a", "b"]  # at least two when available
    assert len(prior_candidates(spread, cfg)) == 5


def test_actual_move_never_changes_the_live_candidate_evaluation():
    board = chess.Board()
    cfg = AnalysisConfig()
    ev = FakeEvaluator()
    live_a, a = analyze_engine(ev, None, board.fen(), ["e2e4", "d2d4"], cfg, actual=None)
    live_b, b = analyze_engine(ev, None, board.fen(), ["e2e4", "d2d4"], cfg, actual="h2h3")
    assert live_a == live_b and a.evals == b.evals and "h2h3" not in live_b
    assert a.actual_eval is None and b.actual_eval.uci == "h2h3"


def make_builder(cache, prior=None, config=None, workers=2):
    evaluators = []

    def factory():
        evaluators.append(FakeEvaluator())
        return evaluators[-1]

    b = DecisionBuilder(prior or FakePrior(), factory, cache, config, workers=workers, chunk=7)
    return b, evaluators


def test_builder_caches_and_invalidates_by_producer_and_settings(conn, tmp_path):
    eps = list(player_episodes(conn, "Alice"))
    cache = AnalysisCache(tmp_path / "c.sqlite3")
    prior = FakePrior()
    b, evs = make_builder(cache, prior)
    first = b.analyze(eps)
    assert prior.calls == len(eps) and sum(e.calls for e in evs) > 0

    b2, evs2 = make_builder(cache, prior)
    prior.calls = 0
    second = b2.analyze(eps)
    assert prior.calls == 0 and sum(e.calls for e in evs2) == 0  # fully cached
    assert [episode_record(x) for x in first] == [episode_record(x) for x in second]

    other_model = FakePrior("fake-prior@2")
    make_builder(cache, other_model)[0].analyze(eps)
    assert other_model.calls == len(eps)  # different model id: no stale reuse

    b4, evs4 = make_builder(cache, prior, AnalysisConfig(nodes=20_000))
    b4.analyze(eps)
    assert sum(e.calls for e in evs4) > 0  # different node budget: engine recomputed


def test_cache_survives_reopen_and_clears_on_schema_change(tmp_path, monkeypatch):
    path = tmp_path / "c.sqlite3"
    c = AnalysisCache(path)
    c.put_prior("k", "m", {"e2e4": -0.1})
    c.close()
    assert AnalysisCache(path).get_prior("k") == {"e2e4": -0.1}
    import pco.decisions.cache as cache_mod

    monkeypatch.setattr(cache_mod, "CACHE_SCHEMA", 99)
    assert cache_mod.AnalysisCache(path).get_prior("k") is None


def test_forced_positions_skip_engine_and_records_are_well_formed(tmp_path):
    # After 1. Qd8+ the back rank is covered and the h6 pawn frees h7: Kh7 is forced.
    c = connect(":memory:")
    pgn = tmp_path / "forced.pgn"
    pgn.write_text(
        '[White "Bob"]\n[Black "Alice"]\n[Result "*"]\n[SetUp "1"]\n'
        '[FEN "6k1/5pp1/7p/8/8/8/5PPP/3Q2K1 w - - 0 1"]\n\n1. Qd8+ Kh7 2. Qd3+ *\n'
    )
    import_records(c, "pgn", str(pgn), pgn_records(pgn), username="Alice")
    eps = list(player_episodes(c, "Alice"))
    assert len(eps) == 1 and eps[0].board().legal_moves.count() == 1
    (a,) = make_builder(AnalysisCache(":memory:"))[0].analyze(eps)
    assert a.engine is None
    rec = episode_record(a)
    assert rec["context"]["forced"] and rec["context"]["missing_engine"]
    assert rec["context"]["missing_clock"] and rec["context"]["missing_self_elo"]


def test_records_include_actual_move_only_as_flagged_label(conn):
    eps = list(player_episodes(conn, "Alice"))
    analyses = make_builder(AnalysisCache(":memory:"), config=AnalysisConfig(k_max=1, mass=0.0))[
        0
    ].analyze(eps)
    outside = [a for a in analyses if not a.actual_in_live and a.engine is not None]
    assert outside, "fixture should contain actual moves outside a 1-candidate live set"
    for a in outside:
        rec = episode_record(a)
        assert rec["feature_version"] == FEATURE_VERSION
        live = [c for c in rec["candidates"] if c["in_live_set"]]
        extra = [c for c in rec["candidates"] if not c["in_live_set"]]
        assert [c["uci"] for c in extra] == [a.episode.actual_move]
        assert extra[0]["separately_evaluated"] is True
        assert all(c["cp_loss"] >= 0 for c in rec["candidates"])
        assert a.episode.actual_move not in [c["uci"] for c in live]
        inference = episode_record(a, include_label=False)  # live-inference view
        assert "actual_move" not in inference
        assert [c["uci"] for c in inference["candidates"]] == list(a.live_candidates)


# ------------------------------------------------------------------ real assets


@pytest.mark.assets
def test_real_build_is_reproducible_from_scratch(conn, maia3_prior, stockfish_path, tmp_path):
    from pco.analysis.stockfish import StockfishEvaluator

    eps = list(player_episodes(conn, "Alice"))
    cfg = AnalysisConfig(nodes=3_000, best_nodes=1_500)

    def build(path, workers):
        cache = AnalysisCache(path)
        b = DecisionBuilder(
            maia3_prior, lambda: StockfishEvaluator(stockfish_path), cache, cfg, workers
        )
        try:
            return [episode_record(a) for a in b.analyze(eps)]
        finally:
            b.close()
            cache.close()

    first = build(tmp_path / "a.sqlite3", 1)
    second = build(tmp_path / "b.sqlite3", 2)  # fresh cache, different parallelism
    assert first == second
    assert all(r["prior_model"].startswith("maia3-5m@") for r in first)
    assert AnalysisLimit(cfg.nodes).nodes == 3_000
