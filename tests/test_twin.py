"""The Twin layer: versions, observations, whispers, reflections, jobs and the library."""

import random
import time
from dataclasses import replace

import chess
import numpy as np
import pytest
from fakes import FakeEvaluator, FakePrior
from homes import make_library, quiet, synthetic_pgn_text

import pco.play.twin as twin_mod
from pco.decisions.cache import AnalysisCache
from pco.play.jobs import JobBusy, JobRunner
from pco.play.library import LibraryError, most_common_name
from pco.play.opponent import OpponentError
from pco.play.server import PlayApp
from pco.play.session import SessionError
from pco.play.twin import TwinService, position_id


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A home with 60 games of "Me" and a Twin built from them (fake prior and engine)."""
    library = make_library(tmp_path_factory.mktemp("home"))
    result = library.import_games(quiet, None, "pgn", None, synthetic_pgn_text())
    assert result == {"player": "Me", "found": 60, "new": 60}
    cache = AnalysisCache(library.settings.analysis_cache_path)
    stages = []
    profile = library.build(lambda stage, detail: stages.append(stage), "Me", cache)
    return library, profile, cache, stages


@pytest.fixture
def twin(built, monkeypatch):
    monkeypatch.setattr(twin_mod, "MIN_VERSION_GAMES", 20)
    library, profile, cache, _ = built
    return TwinService(
        "Me", profile, lambda: library.load_games("Me"), FakePrior(), FakeEvaluator(), cache
    )


def test_build_reports_every_stage_and_records_a_covariance(built):
    _, profile, _, stages = built
    # REMEMBERING repeats (one report per analysis chunk); the order never goes back
    assert [s for i, s in enumerate(stages) if s not in stages[:i]] == [
        "REMEMBERING",
        "FINDING PATTERNS",
        "COMPARING DECISIONS",
        "RECONSTRUCTING YOU",
        "READY",
    ]
    cov = np.array(profile.weight_cov)
    assert cov.shape == (len(profile.weights),) * 2 and np.all(np.diag(cov) >= 0)


def test_versions_use_their_own_openings_and_strength(twin):
    versions = {v["id"]: v for v in twin.versions()}
    # bullet was 2025 only, over a year before the last game: not "you at bullet" any more
    assert set(versions) == {"current", "year-2025", "tempo-blitz"}
    basis = versions["tempo-blitz"]["basis"]
    assert basis == "OPENINGS + STRENGTH · BLITZ · PAST YEAR · STYLE · 2026"
    assert versions["current"]["label"] == "Current You" and versions["current"]["period"] == "2026"
    assert versions["year-2025"]["rating"] == 1300 and versions["tempo-blitz"]["rating"] == 1500
    assert versions["year-2025"]["signature"] == {"move": "1.e4", "share": 1.0}
    assert "STYLE · 2026" in versions["year-2025"]["basis"]
    opp = twin.opponent("year-2025", None, random.Random(1))
    assert opp.profile.elo == 1300 and opp.profile.weights == twin.profile.weights
    games_2025 = [g for g in twin.games() if g.year == "2025"]
    start = twin.seen(chess.Board(), "year-2025")
    assert start == sum(g.white for g in games_2025)  # only that year's games, as White
    with pytest.raises(ValueError):
        twin.opponent("year-1999", None, random.Random(1))


def test_observations_need_clear_evidence(twin):
    p = twin.profile
    names = p.feature_names
    n = len(names)
    w = np.zeros(n)
    w[names.index("castle")] = -1.4
    w[names.index("piece_knight")] = 0.5
    w[names.index("capture")] = 0.3  # real, but too uncertain to mention
    cov = np.eye(n) * 0.01
    cov[names.index("capture"), names.index("capture")] = 0.25
    planted = replace(p, weights=list(w), weight_cov=cov.tolist())
    twin.reload("Me", planted, twin._load_games)
    texts = [o["text"] for o in twin.observations()]
    assert texts[:2] == ["You often delay castling.", "Knights are where your hands go first."]
    assert not any("capture" in t for t in texts)
    assert "As White, you begin with 1.e4 in 100% of your games." in texts
    assert twin.observations()[0]["machine"] == "CASTLE -1.40 · Z 14.0"
    twin.reload("Me", replace(planted, weight_cov=None), twin._load_games)
    assert [o["text"] for o in twin.observations()] == [
        t for t in texts if t.startswith(("As White", "Against"))
    ]


def test_whispers_are_rare_and_grounded(twin):
    memory = {}
    start = chess.Board()
    flat = {m.uci(): -3.0 for m in start.legal_moves}
    # the starting position is familiar: nothing to say about a usual move
    assert twin.whisper(start, "e2e4", {**flat, "e2e4": -0.1}, "current", memory) is None
    # an unseen position after move 1: "New territory." once
    odd = chess.Board()
    for uci in ("a2a3", "h7h6", "b2b3", "g7g6"):
        odd.push_uci(uci)
    logprobs = {m.uci(): -3.0 for m in odd.legal_moves}
    w = twin.whisper(odd, "c2c3", logprobs, "current", memory)
    assert w == {"kind": "new", "position": position_id(odd), "seen": 0}
    # a rare move, but too soon after the last remark
    logprobs[odd.legal_moves.__iter__().__next__().uci()] = -0.01
    assert twin.whisper(odd, "c2c3", logprobs, "current", memory) is None
    later = odd.copy()
    for uci in ("c2c3", "f7f6", "d2d3", "e7e6", "e2e3", "d7d6", "f2f3", "a7a6"):
        later.push_uci(uci)
    rare = {m.uci(): -6.0 for m in later.legal_moves}
    rare["g1h3"] = -0.01
    too_soon = dict(memory, last_ply=6)  # 6 plies ago: still too soon for another remark
    assert twin.whisper(later.copy(), "a3a4", rare, "current", too_soon) is None
    w = twin.whisper(later, "a3a4", rare, "current", memory)
    assert w["kind"] == "unusual" and w["likeness"] < twin_mod.UNUSUAL_LIKENESS
    assert memory["count"] == 2


def test_reflection_compares_your_decisions_with_the_twin(built, twin):
    library, _, cache, _ = built
    app = PlayApp(library, FakePrior(), FakeEvaluator(), cache, rng=random.Random(3))
    app.twin = twin
    app.new_game("current", "white")
    rng = random.Random(4)
    for _ in range(8):
        app.human_move(rng.choice(app.session.legal_moves()))
        app.opponent_move()
    with pytest.raises(SessionError, match="not over"):
        app.reflection()
    app.resign()
    r = app.reflection()
    assert r["outcome"] == "resigned" and r["decisions"] == 8
    assert len(r["positions"]) == len(r["moves"]) + 1 == 17
    assert 0 <= r["match"] <= 1 and r["usual_match"] == twin.profile.validation["model"]["top3"]
    assert 1 <= len(r["divergences"]) <= 5
    for d in r["divergences"]:
        assert d["human"] != d["twin"] and d["verdict"] in ("both", "yours", "its")
        assert d["human_san"].split(".")[0].isdigit() and d["kind_twin_count"] >= 0
        assert d["here_total"] >= d["here_human"] + d["here_twin"] and len(d["kind_twin"]) == 4
    assert app.reflection() is r  # computed once
    assert app.saved_pgn and app.state()["elapsed"] >= 0


def test_jobs_run_one_at_a_time_and_surface_failures():
    jobs = JobRunner()

    def slow(job, say):
        say("REMEMBERING", "DECISION 1 OF 2")
        time.sleep(0.2)
        say("READY", "")
        return {"ok": 1}

    jobs.start("build", slow)
    with pytest.raises(JobBusy):
        jobs.start("build", slow)
    view = jobs.wait()
    assert view["status"] == "done" and view["stages"] == ["REMEMBERING", "READY"]
    assert view["fragments"] == ["DECISION 1 OF 2"] and view["result"] == {"ok": 1}

    def broken(job, say):
        raise ValueError("needs at least ~25 dated games")

    jobs.start("build", broken)
    view = jobs.wait()
    assert view["status"] == "failed" and "25 dated games" in view["error"]


def test_library_preferences_and_pgn_owner(tmp_path):
    library = make_library(tmp_path)
    assert library.prefs() == {"speed": "lite", "workers": None, "temperature": None}
    assert library.set_prefs({"speed": "standard"})["speed"] == "standard"
    assert library.prefs()["speed"] == "standard"
    for bad in ({"speed": "warp"}, {"temperature": 9}, {"color": "red"}, {"workers": 0}):
        with pytest.raises(LibraryError):
            library.set_prefs(bad)
    assert most_common_name(synthetic_pgn_text(6)) == "Me"
    assert library.pick_player() is None and library.players() == []
    with pytest.raises(LibraryError, match="does not play"):
        library.import_games(quiet, None, "pgn", "Nobody", synthetic_pgn_text(4))


def test_a_failing_twin_is_surfaced_and_the_position_is_unchanged(built, twin):
    library, _, cache, _ = built
    app = PlayApp(library, FakePrior(), FakeEvaluator(), cache, rng=random.Random(3))
    app.twin = twin
    app.new_game("current", "black")

    def explode(board):
        raise OpponentError("human prior failed: model exploded")

    app.session.opponent.choose_move = explode
    app.opponent_move()
    state = app.state()
    assert "exploded" in state["error"] and state["opponent_to_move"] and state["san_moves"] == []


def test_live_cache_skips_writes_when_another_process_holds_the_lock(tmp_path):
    import sqlite3

    path = tmp_path / "analysis.sqlite3"
    AnalysisCache(path).close()
    other = sqlite3.connect(path)
    other.execute("BEGIN IMMEDIATE")  # another pco holding the write lock
    live = AnalysisCache(path, busy_timeout=0.05, best_effort=True)
    live.put_prior("k", "m", {"e2e4": -0.1})
    live.commit()
    assert live.skipped_writes >= 1
    strict = AnalysisCache(path, busy_timeout=0.05)
    with pytest.raises(sqlite3.OperationalError):
        strict.put_prior("k", "m", {"e2e4": -0.1})
    other.rollback()
