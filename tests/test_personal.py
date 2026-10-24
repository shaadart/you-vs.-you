"""Residual personalization model: math, normalization, learning, and no-overfit checks."""

import math
import random
from dataclasses import replace

import numpy as np
import pytest
from fakes import FakeEvaluator, FakePrior
from test_evaluation import db, episodes  # noqa: F401  (shared synthetic player fixtures)

from pco.decisions.analysis import AnalysisConfig, DecisionBuilder
from pco.decisions.cache import AnalysisCache
from pco.evaluation.predictors import GenericPrior, mean_nll
from pco.evaluation.splits import chronological_split
from pco.personal.features import CONTEXT, CTX, FEATURES, INDEX, Block, FeatureSpec, featurize
from pco.personal.model import distribution, fit_weights, objective, stack
from pco.personal.predictor import Residual
from pco.priors.base import MoveDistribution


def random_blocks(rng, n=60, d=5):
    blocks = []
    for e in range(n):
        k = int(rng.integers(1, 6))
        x = rng.normal(size=(k, d))
        if e == 0:  # the candidate set covers every legal move: no rest mass
            lp, rest = np.log(rng.dirichlet(np.ones(k))), -math.inf
        else:
            full = np.log(rng.dirichlet(np.ones(k + 3)))
            lp, rest = full[:k], float(np.log(np.exp(full[k:]).sum()))
        label = int(rng.integers(0, k)) if e == 0 else int(rng.integers(-1, k))
        outside = -3.0 if label < 0 else math.nan
        ctx = rng.normal(size=len(CONTEXT))
        blocks.append(Block([f"m{i}" for i in range(k)], x, ctx, lp, rest, label, outside))
    return blocks


def test_gradient_and_hessian_match_finite_differences():
    rng = np.random.default_rng(0)
    s = stack(random_blocks(rng))
    w = rng.normal(size=5) * 0.3
    _, g, h = objective(w, s, 0.1)
    eps = 1e-6
    for i in range(5):
        e = np.eye(5)[i] * eps
        assert (objective(w + e, s, 0.1)[0] - objective(w - e, s, 0.1)[0]) / (
            2 * eps
        ) == pytest.approx(g[i], abs=1e-6)
        num_h = (objective(w + e, s, 0.1)[1] - objective(w - e, s, 0.1)[1]) / (2 * eps)
        assert np.allclose(num_h, h[:, i], atol=1e-6)
    w_hat = fit_weights(s, 1e-3)
    assert np.linalg.norm(objective(w_hat, s, 1e-3)[1]) < 1e-6  # converged to the optimum


def test_zero_weights_reproduce_base_and_distribution_is_normalized():
    base = {"a": math.log(0.5), "b": math.log(0.3), "c": math.log(0.15), "d": math.log(0.05)}
    blk = Block(
        ["a", "b"],
        np.array([[1.0, 0.0], [0.0, 1.0]]),
        np.zeros(len(CONTEXT)),
        np.array([base["a"], base["b"]]),
        math.log(0.2),
        0,
        math.nan,
    )
    same = distribution(np.zeros(2), blk, base)
    assert all(same[m] == pytest.approx(base[m]) for m in base)
    moved = distribution(np.array([2.0, -1.0]), blk, base)
    assert sum(math.exp(v) for v in moved.values()) == pytest.approx(1.0)
    assert moved["a"] > base["a"] and moved["b"] < base["b"]
    # non-candidates keep their relative base proportions
    assert moved["c"] - moved["d"] == pytest.approx(base["c"] - base["d"])


@pytest.fixture(scope="module")
def analyzed(db, episodes):  # noqa: F811
    split = chronological_split(db, episodes)
    b = DecisionBuilder(
        FakePrior(), FakeEvaluator, AnalysisCache(":memory:"), AnalysisConfig(k_max=5, mass=0.999)
    )
    return split, b.analyze(split.train), b.analyze(split.val), b.analyze(split.test)


def relabel(analyses, rule):
    return [
        replace(a, episode=replace(a.episode, actual_move=rule(a))) if a.engine else a
        for a in analyses
    ]


def fit_residual(split, train, val):
    r = Residual(GenericPrior("fake-prior@1"), analyze=lambda eps: train)
    r.fit(split.train, val)
    return r


def worst_candidate(a):
    return min(a.live_candidates, key=lambda m: a.engine.evals[m].cp)


def test_learns_a_planted_preference_and_generalizes(analyzed):
    split, train, val, test = analyzed
    train, val, test = (relabel(x, worst_candidate) for x in (train, val, test))
    r = fit_residual(split, train, val)
    assert r.w[INDEX["cp_loss"]] + r.w[INDEX["wp_loss"]] > 0.5  # "prefers bad moves"
    choice = [a for a in test if a.engine]
    base_nll = mean_nll([a.dist.logprobs for a in choice], choice)
    model_nll = mean_nll(r.predict(choice), choice)
    assert model_nll < base_nll - 0.5  # large held-out gain on a real, consistent pattern


def test_does_not_overfit_noise(analyzed):
    split, train, val, test = analyzed
    rng = random.Random(3)

    def from_base(a):  # labels drawn from the base itself: nothing to learn
        return MoveDistribution("x", a.dist.logprobs).sample(rng, 1.0)

    train, val, test = (relabel(x, from_base) for x in (train, val, test))
    r = fit_residual(split, train, val)
    choice = [a for a in test if a.engine]
    base_nll = mean_nll([a.dist.logprobs for a in choice], choice)
    assert mean_nll(r.predict(choice), choice) < base_nll + 0.05


def test_features_never_depend_on_the_actual_move(analyzed):
    _, train, _, _ = analyzed
    a = next(x for x in train if x.engine and not x.actual_in_live)
    with_label = featurize(a, a.dist.logprobs, a.episode.actual_move)
    without = featurize(a, a.dist.logprobs)
    assert np.array_equal(with_label.x, without.x) and with_label.moves == without.moves
    assert with_label.label == -1 and a.episode.actual_move not in with_label.moves
    assert len(FEATURES) == with_label.x.shape[1]


# ------------------------------------------------------------------ profile & live opponent


@pytest.fixture(scope="module")
def trained(db, tmp_path_factory):  # noqa: F811
    from pco.personal.profile import train_profile

    prior = FakePrior()
    cache = AnalysisCache(":memory:")
    profile = train_profile(db, "Me", prior, FakeEvaluator, cache)
    return profile, prior, cache, tmp_path_factory.mktemp("profiles")


def test_profile_roundtrip_and_version_guards(trained):
    from pco.personal.profile import PersonalProfile

    profile, _, _, root = trained
    path = profile.save(root)
    loaded = PersonalProfile.latest(root, "Me")
    assert loaded == profile and path.exists()
    assert loaded.elo == 1500 and loaded.play_temperature in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
    assert len(loaded.weights) == len(FEATURES) and loaded.book  # repertoire captured
    bad = path.with_name("profile-99999999T000000Z.json")
    from pco.personal.features import PERSONAL_FEATURE_VERSION as V

    bad.write_text(path.read_text().replace(f'"feature_version": {V}', '"feature_version": 0'))
    with pytest.raises(ValueError, match="retrain"):
        PersonalProfile.latest(root, "Me")
    bad.unlink()
    with pytest.raises(FileNotFoundError, match="pco train"):
        PersonalProfile.latest(root, "Nobody")


def test_live_opponent_matches_the_trained_model_exactly(db, episodes, trained):  # noqa: F811
    """Training/inference consistency: same candidates, features and distribution."""
    from dataclasses import replace as dc_replace

    from pco.evaluation.predictors import EloOffset
    from pco.personal.opponent import PersonalOpponent

    profile, prior, cache, _ = trained
    no_book = dc_replace(profile, book={})
    opp = PersonalOpponent(no_book, prior, FakeEvaluator(), cache)
    split = chronological_split(db, episodes)
    analyses = DecisionBuilder(prior, FakeEvaluator, cache, AnalysisConfig()).analyze(split.test)
    r = Residual(EloOffset(prior, cache), analyze=lambda e: [])
    r.base.offset, r.w = profile.elo_offset, profile.w
    checked = 0
    for a, offline in zip(analyses, r.predict(analyses), strict=True):
        if a.engine is None:
            continue
        live = opp.distribution(a.episode.board())
        assert live.keys() == offline.keys()
        worst = max(abs(live[m] - offline[m]) for m in live)
        assert worst < 1e-9, (worst, a.episode.ply)
        checked += 1
    assert checked > 20


def test_personal_opponent_plays_a_complete_legal_game(trained):
    import chess

    from pco.personal.opponent import PersonalOpponent
    from pco.play.session import GameSession

    profile, prior, cache, _ = trained
    opp = PersonalOpponent(profile, prior, FakeEvaluator(), cache, rng=random.Random(5))
    assert "Me" in opp.label and "not the player" in opp.label
    s = GameSession(opp, chess.WHITE)
    rng = random.Random(6)
    while s.over is None and len(s.board.move_stack) < 300:
        if s.human_to_move:
            s.play_human(rng.choice(s.legal_moves()))
        else:
            c = s.play_opponent()
            assert 0 < c.probability <= 1 and 0 < c.likeness <= 1
    assert len(s.board.move_stack) > 10


def test_profile_rejects_a_different_prior(trained):
    from pco.personal.opponent import PersonalOpponent
    from pco.play.opponent import OpponentError

    profile, _, cache, _ = trained
    with pytest.raises(OpponentError, match="retrain"):
        PersonalOpponent(profile, FakePrior("other@9"), FakeEvaluator(), cache)


# ------------------------------------------------------------------ Iteration 6 additions


def test_weighted_objective_gradient_and_warm_start():
    rng = np.random.default_rng(1)
    blocks = random_blocks(rng)
    weights = rng.uniform(0.1, 2.0, len(blocks))
    s_w = stack(blocks, weights=weights)
    w = rng.normal(size=5) * 0.3
    _, g, h = objective(w, s_w, 0.05)
    eps = 1e-6
    for i in range(5):
        e = np.eye(5)[i] * eps
        num = (objective(w + e, s_w, 0.05)[0] - objective(w - e, s_w, 0.05)[0]) / (2 * eps)
        assert num == pytest.approx(g[i], abs=1e-6)
        assert np.allclose(
            (objective(w + e, s_w, 0.05)[1] - objective(w - e, s_w, 0.05)[1]) / (2 * eps),
            h[:, i],
            atol=1e-6,
        )
    cold = fit_weights(s_w, 1e-3)
    warm = fit_weights(s_w, 1e-3, w0=fit_weights(s_w, 1e-1))
    assert np.allclose(cold, warm, atol=1e-5)  # same optimum (convex), fewer iterations


def test_feature_spec_expansion_and_v1_compatibility():
    rng = np.random.default_rng(2)
    x = rng.normal(size=(4, len(FEATURES)))
    ctx = rng.normal(size=(4, len(CONTEXT)))
    assert np.array_equal(FeatureSpec().expand(x, ctx), x)  # v1: no interactions
    spec = FeatureSpec(("time_control", "opponent"))
    full = spec.expand(x, ctx)
    assert full.shape[1] == len(spec.names) and full[:, : len(FEATURES)].tolist() == x.tolist()
    j = spec.names.index("cp_loss*elo_diff")
    assert np.allclose(full[:, j], x[:, INDEX["cp_loss"]] * ctx[:, CTX["elo_diff"]])
    with pytest.raises(ValueError, match="unknown"):
        FeatureSpec(("nonsense",))


def test_recency_weights_halve_per_half_life():
    from pco.personal.predictor import recency_weights

    idx = np.array([0, 50, 100, 100])
    assert recency_weights(idx, None).tolist() == [1, 1, 1, 1]
    assert recency_weights(idx, 50).tolist() == pytest.approx([0.25, 0.5, 1.0, 1.0])


def test_contextual_live_opponent_still_matches_offline(db, episodes, trained):  # noqa: F811
    """Exactness must hold with interaction groups too (the train/serve contract)."""
    from dataclasses import replace as dc_replace

    from pco.evaluation.predictors import EloOffset
    from pco.personal.opponent import PersonalOpponent

    profile, prior, cache, _ = trained
    spec = FeatureSpec(("opponent", "phase"))
    rng = np.random.default_rng(4)
    w = rng.normal(size=len(spec.names)) * 0.2
    prof = dc_replace(
        profile,
        book={},
        feature_groups=list(spec.groups),
        feature_names=spec.names,
        weights=list(w),
    )
    opp = PersonalOpponent(prof, prior, FakeEvaluator(), cache)
    split = chronological_split(db, episodes)
    analyses = DecisionBuilder(prior, FakeEvaluator, cache, AnalysisConfig()).analyze(split.test)
    r = Residual(EloOffset(prior, cache), analyze=lambda e: [], spec=spec)
    r.base.offset, r.w = prof.elo_offset, w
    for a, offline in zip(analyses, r.predict(analyses), strict=True):
        if a.engine is not None:
            live = opp.distribution(a.episode.board())
            assert max(abs(live[m] - offline[m]) for m in live) < 1e-9


def synthetic_player(rng, w_old, w_new, n_games=460, per_game=10, switch=200):
    """Blocks with labels *sampled* from a known residual model; games >= `switch` use w_new."""
    blocks, index = [], []
    for g in range(n_games):
        w = w_old if g < switch else w_new
        for _ in range(per_game):
            k = int(rng.integers(2, 6))
            x = rng.normal(size=(k, len(FEATURES)))
            full = np.log(rng.dirichlet(np.ones(k + 2)))
            lp, rest = full[:k], float(np.log(np.exp(full[k:]).sum()))
            logits = np.append(lp + x @ w, rest)
            p = np.exp(logits - logits.max())
            choice = int(rng.choice(k + 1, p=p / p.sum()))
            label = choice if choice < k else -1
            blocks.append(
                Block([f"m{i}" for i in range(k)], x, np.zeros(len(CONTEXT)), lp, rest, label, rest)
            )
            index.append(g)
    return blocks, np.array(index)


def test_behavior_drift_detects_a_planted_change_and_not_a_stable_player():
    from pco.personal.drift import behavior_drift

    d = len(FEATURES)
    w_a = np.zeros(d)
    w_a[INDEX["capture"]], w_a[INDEX["castle"]] = 1.0, -1.0
    w_b = np.zeros(d)
    w_b[INDEX["capture"]], w_b[INDEX["castle"]] = -1.0, 1.0

    def run(w_old, w_new, seed):
        blocks, index = synthetic_player(np.random.default_rng(seed), w_old, w_new)
        train = index < 400
        tb = [b for b, t in zip(blocks, train, strict=True) if t]
        vb = [b for b, t in zip(blocks, train, strict=True) if not t]
        return behavior_drift(tb, index[train], vb, index[~train], FeatureSpec(), 1e-3)

    shifted = run(w_a, w_b, 0)  # changed style at game 200 (inside the training window)
    assert shifted["status"] == "change detected"
    assert set(shifted["largest_weight_changes"]) >= {"capture", "castle"}
    for seed in (1, 2, 3):  # a stable player is not flagged
        assert run(w_a, w_a, seed)["status"] == "no clear change"
    tiny = behavior_drift([], np.array([]), [], np.array([]), FeatureSpec(), 1e-3)
    assert tiny["status"] == "insufficient data"


def test_train_on_recent_games_only(db):  # noqa: F811
    from pco.personal.profile import train_profile

    p = train_profile(
        db, "Me", FakePrior(), FakeEvaluator, AnalysisCache(":memory:"), recent_games=30
    )
    assert sum(p.data["games"].values()) <= 30
    assert p.data["last_game"] is not None
