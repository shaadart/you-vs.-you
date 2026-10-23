"""End-to-end evaluation of move predictors on a player's held-out future games."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from pco import __version__
from pco.analysis.stockfish import Evaluator
from pco.decisions.analysis import AnalysisConfig, DecisionBuilder
from pco.decisions.cache import AnalysisCache
from pco.decisions.episodes import player_episodes
from pco.decisions.features import FEATURE_VERSION
from pco.evaluation.metrics import bootstrap_diff, breakdowns, per_example, summarize
from pco.evaluation.predictors import Book, EloOffset, GenericPrior, Predictor, Temperature, Uniform
from pco.evaluation.quality import MoveScorer, quality_matching_temperature, quality_sweep
from pco.evaluation.splits import SplitSpec, chronological_split
from pco.priors.base import HumanPrior

REFERENCE = "maia"  # bootstrap differences are reported against generic Maia


@dataclass(frozen=True)
class EvalConfig:
    split: SplitSpec = field(default_factory=SplitSpec)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    temperatures: tuple[float, ...] = (0.0, 0.5, 0.6, 0.7, 0.8, 1.0)
    samples: int = 2  # sampled moves per position and temperature
    quality: bool = True
    n_boot: int = 1000
    seed: int = 0


def make_predictors(prior: HumanPrior, cache: AnalysisCache) -> list[Predictor]:
    return [
        Uniform(),
        GenericPrior(prior.model_id),
        Temperature(GenericPrior(prior.model_id)),
        EloOffset(prior, cache),
        Temperature(EloOffset(prior, cache)),
        Book(Temperature(EloOffset(prior, cache))),
    ]


def quality_predictors(prior: HumanPrior, cache: AnalysisCache) -> list[Predictor]:
    """No NLL temperature here: the sweep itself scans sampling temperature."""
    return [GenericPrior(prior.model_id), EloOffset(prior, cache), Book(EloOffset(prior, cache))]


def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


def run_evaluation(
    conn,
    player: str,
    prior: HumanPrior,
    make_evaluator: Callable[[], Evaluator],
    cache: AnalysisCache,
    config: EvalConfig | None = None,
    workers: int = 1,
    max_games: int | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    cfg = config or EvalConfig()
    timing: dict[str, float] = {}
    t = time.perf_counter()
    split = chronological_split(conn, list(player_episodes(conn, player, max_games)), cfg.split)
    if not split.val or not split.test:
        raise ValueError(f"not enough dated games for {player!r} to form val/test splits")

    builder = DecisionBuilder(prior, make_evaluator, cache, cfg.analysis, workers)
    try:
        val = builder.analyze(split.val, progress)
        test = builder.analyze(split.test, progress)
        timing["analysis_s"] = time.perf_counter() - t
        scorer = MoveScorer(builder.evaluators[0], cache, cfg.analysis)
        report = _evaluate(cfg, split, val, test, prior, cache, scorer, timing)
    finally:
        builder.close()
        cache.commit()
    report.update(
        {
            "player": player,
            "pco_version": __version__,
            "prior_model": prior.model_id,
            "engine": builder.evaluators[0].engine_id,
            "feature_version": FEATURE_VERSION,
            "eval_config": asdict(cfg),
            "split": split.summary(),
            "timing": timing,
        }
    )
    return _clean(report)


def _evaluate(cfg, split, val, test, prior, cache, scorer, timing) -> dict[str, Any]:
    choice = [a for a in test if a.engine is not None]  # forced moves carry no behavior
    game_ids = np.array([a.episode.game_id for a in choice])
    book_positions = set(Book.build_counts(split.train))
    results: dict[str, Any] = {}
    examples: dict[str, dict[str, np.ndarray]] = {}
    for p in make_predictors(prior, cache):
        t = time.perf_counter()
        p.fit(split.train, val)
        timing[f"fit_s[{p.name}]"] = time.perf_counter() - t
        t = time.perf_counter()
        dists = p.predict(choice)
        timing[f"predict_ms_per_decision[{p.name}]"] = (
            1000 * (time.perf_counter() - t) / len(choice)
        )
        ex = per_example(dists, choice)
        examples[p.name] = ex
        results[p.name] = {
            "params": p.params(),
            "test": summarize(ex),
            "breakdowns": breakdowns(ex, choice, book_positions),
        }
    ref = examples[REFERENCE]
    for name, ex in examples.items():
        if name != REFERENCE:
            results[name]["vs_maia"] = {
                "nll": bootstrap_diff(ex, ref, game_ids, "nll", cfg.n_boot, cfg.seed),
                "top1": bootstrap_diff(ex, ref, game_ids, "top1", cfg.n_boot, cfg.seed),
            }

    quality: dict[str, Any] = {}
    if cfg.quality:
        t = time.perf_counter()
        val_choice = [a for a in val if a.engine is not None]
        for p in quality_predictors(prior, cache):
            p.fit(split.train, val)
            on_val = quality_sweep(
                p.predict(val_choice), val_choice, scorer, cfg.temperatures, cfg.samples, cfg.seed
            )
            chosen = quality_matching_temperature(on_val)
            on_test = quality_sweep(
                p.predict(choice), choice, scorer, cfg.temperatures, cfg.samples, cfg.seed
            )
            quality[p.name] = {
                "temperature_chosen_on_val": chosen,
                "test": on_test,
                "test_at_chosen": on_test["sampled"][str(chosen)],
            }
        timing["quality_s"] = time.perf_counter() - t

    n_forced = len(test) - len(choice)
    return {
        "test_decisions": {"total": len(test), "forced_excluded": n_forced, "scored": len(choice)},
        "predictors": results,
        "quality": quality,
    }


def format_report(r: dict[str, Any]) -> str:
    lines = []
    sp = r["split"]
    lines.append(
        f"player {r['player']}  prior {r['prior_model']}  engine {r['engine']}  "
        f"split {sp['fingerprint']}"
    )
    lines.append(
        f"games train/val/test {sp['games']['train']}/{sp['games']['val']}/{sp['games']['test']}"
        f"  (test from {sp['first_date']['test']}; excluded {sp['excluded_games']})"
    )
    td = r["test_decisions"]
    lines.append(f"test decisions scored {td['scored']} (forced excluded: {td['forced_excluded']})")
    lines.append("")
    lines.append(
        f"{'predictor':28s} {'NLL':>7s} {'top1':>6s} {'top3':>6s} {'ECE':>6s}  "
        f"{'dNLL vs maia [95% CI]':>27s}  {'in-book top1/NLL':>17s}  {'out-of-book top1/NLL':>20s}"
    )
    for name, res in r["predictors"].items():
        s = res["test"]
        vs = res.get("vs_maia", {}).get("nll")
        vs_txt = (
            f"{vs['mean']:+.4f} [{vs['ci95'][0]:+.4f},{vs['ci95'][1]:+.4f}]"
            if vs
            else "(reference)"
        )
        book = res["breakdowns"]["book"]
        ib, ob = book.get("in_book", {"n": 0}), book.get("out_of_book", {"n": 0})
        ib_txt = f"{ib['top1']:.3f}/{ib['nll']:.3f}" if ib["n"] else "-"
        ob_txt = f"{ob['top1']:.3f}/{ob['nll']:.3f}" if ob["n"] else "-"
        lines.append(
            f"{name:28s} {s['nll']:7.4f} {s['top1']:6.3f} {s['top3']:6.3f} {s['ece']:6.3f}  "
            f"{vs_txt:>27s}  {ib_txt:>17s}  {ob_txt:>20s}"
        )
    book = r["predictors"][REFERENCE]["breakdowns"]["book"]
    lines.append(
        f"(in-book = position seen in the player's training games: "
        f"{book.get('in_book', {'n': 0})['n']} of {td['scored']} test decisions)"
    )
    for name, res in r["predictors"].items():
        if res["params"]:
            lines.append(f"  {name}: {_brief(res['params'])}")
    if r["quality"]:
        lines.append("")
        lines.append(
            "sampled-quality match on test (mean cp loss / blunder rate; actual player first)"
        )
        for name, q in r["quality"].items():
            a = q["test"]["actual"]
            cells = "  ".join(
                f"T{t}:{v['mean_cp_loss']:.0f}/{v['blunder_rate']:.3f}"
                for t, v in q["test"]["sampled"].items()
            )
            lines.append(
                f"  {name:14s} actual {a['mean_cp_loss']:.0f}/{a['blunder_rate']:.3f}  {cells}"
                f"  -> T chosen on val: {q['temperature_chosen_on_val']}"
            )
    return "\n".join(lines)


def _brief(params: dict[str, Any]) -> str:
    keep = {k: v for k, v in params.items() if k not in ("val_nll_by_offset",)}
    if "base" in keep:
        keep["base"] = _brief(keep["base"])
    return ", ".join(f"{k}={v}" for k, v in keep.items())
