"""Command-line entry point.

`pco setup` installs assets, `pco doctor` checks them, `pco import ...` loads games,
`pco games` summarizes what is stored, `pco play` opens a local game in the browser, and
`pco build` turns a player's games into analyzed decision episodes, and `pco eval`
measures move predictors on the player's held-out future games.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import chess

from pco.assets import MAIA3_5M, AssetError, ensure_asset, stockfish_asset, stockfish_binary
from pco.config import Settings, load_settings
from pco.importers.http import Http, UrllibHttp
from pco.importers.pipeline import ImportReport, import_records
from pco.importers.sources import SourceError, pgn_records
from pco.presets import DEFAULT_RECENT_GAMES, DEFAULT_SPEED, PRESETS, preset
from pco.storage import games as repo
from pco.storage.db import SchemaError, connect


def cmd_setup(settings: Settings) -> int:
    assets = [MAIA3_5M] + ([a] if (a := stockfish_asset()) else [])
    for asset in assets:
        print(f"{asset.name}: ", end="", flush=True)
        path = ensure_asset(settings, asset)
        print(f"ok ({path}) [{asset.license}]")
    if not stockfish_asset():
        print("stockfish: no pinned build for this platform; put it on PATH or set PCO_STOCKFISH")
    return 0


def _print_data_summary(settings: Settings) -> None:
    from pco.personal.profile import PersonalProfile
    from pco.presets import PRESETS

    if settings.db_path.exists():
        conn = connect(settings.db_path)
        try:
            for p in repo.player_summary(conn):
                print(f"player {p['name']}: {p['games']} games ({', '.join(p['accounts'])})")
        finally:
            conn.close()
    for directory in (
        sorted(settings.profiles_dir.glob("*")) if settings.profiles_dir.exists() else []
    ):
        try:
            prof = PersonalProfile.latest(settings.profiles_dir, directory.name)
        except (FileNotFoundError, ValueError) as exc:
            print(f"profile {directory.name}: unusable ({exc})")
            continue
        speed = next(
            (n for n, sp in PRESETS.items() if vars(sp.analysis) == prof.analysis_config), "custom"
        )
        print(
            f"profile {prof.player}: trained {prof.created_at}, speed {speed},"
            f" play temperature {prof.play_temperature:g}"
        )
    cache = settings.analysis_cache_path
    if cache.exists():
        print(
            f"analysis cache: {cache.stat().st_size / 1e6:.0f} MB"
            " (safe to delete; rebuilt on demand)"
        )


def cmd_doctor(settings: Settings) -> int:
    from pco.analysis.stockfish import AnalysisLimit, StockfishEvaluator
    from pco.priors.base import PriorRequest
    from pco.priors.maia3 import Maia3Prior

    print(f"home: {settings.home}")
    _print_data_summary(settings)
    ok = True
    board = chess.Board()
    for san in ["e4", "e5", "Nf3", "Nc6"]:
        board.push_san(san)
    try:
        t = time.perf_counter()
        prior = Maia3Prior(
            ensure_asset(settings, MAIA3_5M, offline=True), threads=settings.torch_threads
        )
        load = time.perf_counter() - t
        t = time.perf_counter()
        dist = prior.predict([PriorRequest(board, 1500, 1500)])[0]
        infer = time.perf_counter() - t
        top = ", ".join(f"{m} {p:.2f}" for m, p in dist.top(3))
        print(f"prior {prior.model_id}: load {load:.2f}s, infer {infer * 1000:.0f}ms; top: {top}")
    except AssetError as exc:
        ok = False
        print(f"prior: MISSING - {exc}")
    try:
        with StockfishEvaluator(stockfish_binary(settings)) as sf:
            t = time.perf_counter()
            evals = sf.evaluate(board, ["f1b5", "f1c4", "d2d4"], AnalysisLimit(nodes=20_000))
            took = time.perf_counter() - t
            best = ", ".join(f"{e.uci} {e.cp:+d}" for e in evals)
            print(f"engine {sf.engine_id}: 3 candidates @20k nodes in {took * 1000:.0f}ms; {best}")
    except AssetError as exc:
        ok = False
        print(f"engine: MISSING - {exc}")
    return 0 if ok else 1


def _print_report(report: ImportReport, username: str | None) -> None:
    print(
        f"import run {report.run_id}: {report.seen} records, {report.new} new games, "
        f"{report.duplicate} duplicates, {report.rejected} rejected"
    )
    if username:
        print(f"  games involving {username}: {report.involving_player}")
    for err in report.errors:
        print(f"  rejected {err}")
    if report.rejected > len(report.errors):
        print(f"  ... and {report.rejected - len(report.errors)} more rejected")


def cmd_import(settings: Settings, args: argparse.Namespace, http: Http) -> int:
    from pco.importers.accounts import import_account

    conn = connect(settings.db_path)
    try:
        if args.source == "pgn":
            report = import_records(
                conn,
                "pgn",
                str(Path(args.file).resolve()),
                pgn_records(Path(args.file)),
                username=args.username,
                player=args.player,
            )
            username = args.username
        else:
            username = args.username
            report, last = import_account(
                conn,
                http,
                settings,
                args.source,
                username,
                player=args.player,
                full=args.full,
                max_games=getattr(args, "max", None),
            )
            if last:
                print(f"incremental: new games since last import ({last}); --full refetches all")
        _print_report(report, username)
        return 0
    except (SourceError, repo.AccountConflict) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def cmd_games(settings: Settings, verify: bool = False) -> int:
    conn = connect(settings.db_path)
    try:
        total = conn.execute("SELECT count(*) FROM games").fetchone()[0]
        rejected = conn.execute(
            "SELECT count(*) FROM raw_games WHERE normalize_error IS NOT NULL"
        ).fetchone()[0]
        print(f"database: {settings.db_path}")
        print(f"games stored: {total} (raw records failing normalization: {rejected})")
        for p in repo.player_summary(conn):
            span = f"{p['first']} .. {p['last']}" if p["first"] else "no dates"
            print(
                f"\nplayer {p['name']} ({', '.join(p['accounts'])}): {p['games']} games, "
                f"{p['decisions']} own moves, {span}"
            )
            for r in p["breakdown"]:
                print(f"  {r['platform']:9s} {r['tc']:14s} {r['color']:5s} {r['n']:6d} games")
        if verify:
            n, failures = repo.verify_games(conn)
            print(f"\nverify: replayed {n} games, {len(failures)} failures")
            for f in failures[:20]:
                print(f"  {f}")
            return 1 if failures else 0
        return 0
    finally:
        conn.close()


def cmd_play(settings: Settings, args: argparse.Namespace) -> int:
    """The interface: play your Twin, bring in games, rebuild, look back (docs/DESIGN.md)."""
    import webbrowser

    from pco.analysis.stockfish import StockfishEvaluator
    from pco.decisions.cache import AnalysisCache
    from pco.play.library import Library, LibraryError, real_trainer
    from pco.play.server import PlayApp, make_server
    from pco.priors.maia3 import Maia3Prior

    prior = Maia3Prior(
        ensure_asset(settings, MAIA3_5M, offline=True), threads=settings.live_threads
    )
    evaluator = StockfishEvaluator(stockfish_binary(settings))
    # Live play must not stall on another pco process holding the cache: best-effort writes
    cache = AnalysisCache(settings.analysis_cache_path, busy_timeout=0.1, best_effort=True)

    def close() -> None:
        evaluator.close()
        cache.close()

    library = Library(settings, UrllibHttp(), real_trainer(settings))
    try:
        app = PlayApp(
            library,
            prior,
            evaluator,
            cache,
            player=args.player,
            rng=random.Random(args.seed),
            temperature=args.temperature,
        )
        server = make_server(app, args.port)
    except LibraryError as exc:
        print(f"error: {exc} (see `pco games`)", file=sys.stderr)
        close()
        return 1
    except OSError as exc:
        print(f"error: cannot listen on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        close()
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    who = app.twin.player
    if who is None:
        what = "no games yet: the page will ask for them"
    elif app.twin.has_twin:
        what = f"{who}, Twin built {app.twin.profile.created_at}"
    else:
        what = f"{who}, no Twin yet: the page can build one"
    print(f"pco play: {url}  ({what})")
    print("Ctrl-C to stop.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
        close()
    return 0


def cmd_train(settings: Settings, args: argparse.Namespace) -> int:
    from pco.analysis.stockfish import StockfishEvaluator
    from pco.decisions.cache import AnalysisCache
    from pco.personal.profile import PersonalProfile, train_profile
    from pco.priors.maia3 import Maia3Prior

    conn = connect(settings.db_path)
    if conn.execute("SELECT 1 FROM players WHERE name=?", (args.player,)).fetchone() is None:
        print(f"error: no player named {args.player!r} (see `pco games`)", file=sys.stderr)
        return 1
    prior = Maia3Prior(
        ensure_asset(settings, MAIA3_5M, offline=True), threads=settings.torch_threads
    )
    sf_path = stockfish_binary(settings)
    workers = args.workers or max(1, min(4, (os.cpu_count() or 2) - 2))
    cache = AnalysisCache(settings.analysis_cache_path)
    print(f"training a personal profile for {args.player} (analysis is cached)", flush=True)
    try:
        previous = PersonalProfile.latest(settings.profiles_dir, args.player)
    except (FileNotFoundError, ValueError):
        previous = None  # first profile, or an outdated format: full search
    try:
        profile = train_profile(
            conn,
            args.player,
            prior,
            lambda: StockfishEvaluator(sf_path),
            cache,
            workers=workers,
            progress=True,
            previous=previous,
            recent_games=None if args.all else args.recent,
            config=preset(args.speed).analysis,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "\ninterrupted: the analysis finished so far is cached; run the same command"
            " again to resume",
            file=sys.stderr,
        )
        return 130
    finally:
        cache.close()
        conn.close()
    path = profile.save(settings.profiles_dir)
    v = profile.validation
    print(f"profile: {path}")
    print(
        f"  data: {profile.data['games']} games, {profile.data['decisions']} decisions,"
        f" last game {profile.data['last_game']}"
    )
    print(
        f"  validation (newest 10% of games): NLL {v['model']['nll']:.4f} vs generic Maia"
        f" {v['generic_maia']['nll']:.4f}; top-1 {v['model']['top1']:.3f} vs"
        f" {v['generic_maia']['top1']:.3f}"
    )
    print(
        f"  rating {profile.elo} (offset {profile.elo_offset:+d}), regularization"
        f" {profile.reg_lambda:g}, book alpha {profile.book_alpha:g},"
        f" play temperature {profile.play_temperature:g}"
    )
    c = profile.data.get("cache", {})
    if c:
        print(
            f"  work: {profile.data['train_seconds']:.0f}s; engine searches computed"
            f" {c['computed'].get('engine', 0)} (cached {c['hits'].get('engine', 0)}),"
            f" prior evaluations computed {c['computed'].get('prior', 0)}"
            f" (cached {c['hits'].get('prior', 0)})"
        )
    hl = profile.half_life_games
    print(
        "  recency: "
        + (
            f"recent games weighted (half-life {hl:g} games)"
            if hl
            else "all games weighted equally"
        )
        + f"; features: v1 + {', '.join(profile.feature_groups) or 'none'}"
    )
    drift = v.get("behavior_drift", {})
    if drift.get("status") in ("change detected", "no clear change"):
        g = drift["recent_model_nll_gain"]
        changed = ", ".join(f"{k} {d:+.2f}" for k, d in drift["largest_weight_changes"].items())
        print(
            f"  behavior over time: {drift['status']} (newer-half model better on recent games by"
            f" {g['mean']:+.4f} NLL, 95% CI [{g['ci95'][0]:+.4f}, {g['ci95'][1]:+.4f}]);"
            f" largest shifts: {changed}"
        )
    print("  (held-out *test* claims come from `pco eval`, not from this validation split)")
    return 0


TRAIN_OVERHEAD = 0.6  # measured: total training throughput / analysis throughput (E7.3)


def cmd_quickstart(settings: Settings, args: argparse.Namespace, http: Http) -> int:
    """Chess.com/Lichess account(s) -> assets -> import -> train -> play, in one command."""
    from pco.decisions.episodes import player_episodes

    accounts = [("chesscom", args.chesscom), ("lichess", args.lichess)]
    accounts = [(platform, name) for platform, name in accounts if name]
    if not accounts:
        print("error: give --chesscom USER and/or --lichess USER", file=sys.stderr)
        return 2
    player = args.player or accounts[0][1]
    print("[1/4] engine and model files")
    if cmd_setup(settings) != 0:
        return 1
    print(f"[2/4] importing games for player {player!r}")
    for platform, name in accounts:
        ns = argparse.Namespace(source=platform, username=name, player=player, full=False, max=None)
        if cmd_import(settings, ns, http) != 0:
            return 1
    conn = connect(settings.db_path)
    try:
        episodes = list(player_episodes(conn, player))
    finally:
        conn.close()
    dated = sorted({e.game_index for e in episodes if e.played_at})
    keep = set(dated if args.all else dated[-args.recent :])
    n_games, n_decisions = len(keep), sum(e.game_index in keep for e in episodes)
    # whole first training ~ analysis rate x 0.6 (rating fit, temperature sweep; E7.3)
    minutes = n_decisions / (preset(args.speed).est_decisions_per_s * TRAIN_OVERHEAD) / 60
    print(
        f"[3/4] training on {n_games} games ({n_decisions} of your moves), speed {args.speed}:"
        f" first time about {max(1, round(minutes))} min; later runs only analyze new games"
    )
    train_args = argparse.Namespace(
        player=player, workers=args.workers, recent=args.recent, all=args.all, speed=args.speed
    )
    code = cmd_train(settings, train_args)
    if code != 0:
        return code
    print(f"[4/4] ready: play against yourself with\n    uv run pco play --player {player}")
    if args.play:
        play_args = argparse.Namespace(
            player=player, port=8765, temperature=None, seed=None, no_browser=args.no_browser
        )
        return cmd_play(settings, play_args)
    return 0


def cmd_build(settings: Settings, args: argparse.Namespace) -> int:
    from pco.analysis.stockfish import StockfishEvaluator
    from pco.decisions.analysis import AnalysisConfig, DecisionBuilder
    from pco.decisions.cache import AnalysisCache
    from pco.decisions.episodes import player_episodes
    from pco.decisions.features import episode_record
    from pco.priors.maia3 import Maia3Prior

    conn = connect(settings.db_path)
    try:
        if conn.execute("SELECT 1 FROM players WHERE name=?", (args.player,)).fetchone() is None:
            print(f"error: no player named {args.player!r} (see `pco games`)", file=sys.stderr)
            return 1
        episodes = list(player_episodes(conn, args.player, args.max_games))
    finally:
        conn.close()
    if not episodes:
        print(f"player {args.player!r} has no decisions to analyze")
        return 0
    base_cfg = preset(args.speed).analysis
    config = AnalysisConfig(
        k_max=args.k or base_cfg.k_max,
        mass=args.mass or base_cfg.mass,
        nodes=args.nodes or base_cfg.nodes,
        best_nodes=args.best_nodes or base_cfg.best_nodes,
    )
    prior = Maia3Prior(
        ensure_asset(settings, MAIA3_5M, offline=True), threads=settings.torch_threads
    )
    sf_path = stockfish_binary(settings)
    workers = args.workers or max(1, min(4, (os.cpu_count() or 2) - 2))
    cache = AnalysisCache(settings.analysis_cache_path)
    builder = DecisionBuilder(prior, lambda: StockfishEvaluator(sf_path), cache, config, workers)
    n_games = len({e.game_id for e in episodes})
    print(
        f"building {len(episodes)} decisions from {n_games} games of {args.player} "
        f"({config}, {workers} engine workers)",
        flush=True,
    )
    t = time.perf_counter()
    try:
        analyses = builder.analyze(episodes, progress=True)
    except KeyboardInterrupt:
        print("interrupted: finished chunks are cached; rerun to resume", file=sys.stderr)
        return 130
    finally:
        builder.close()
        cache.close()
    took = time.perf_counter() - t
    forced = sum(a.engine is None for a in analyses)
    in_live = sum(a.actual_in_live for a in analyses)
    no_clock = sum(a.episode.clock_self_s is None for a in analyses)
    elo_src: dict[str, int] = {}
    for a in analyses:
        elo_src[a.episode.self_elo_source] = elo_src.get(a.episode.self_elo_source, 0) + 1
    n = len(analyses)
    print(f"done in {took:.1f}s ({n / took:.0f} decisions/s)")
    print(f"  forced (single legal move): {forced}")
    print(f"  actual move inside live candidate set: {in_live / n:.3f}")
    print(f"  missing clock: {no_clock / n:.3f}   self-elo source: {elo_src}")
    print(
        f"  cache hits/misses: prior {cache.hits['prior']}/{cache.misses['prior']}, "
        f"engine {cache.hits['engine']}/{cache.misses['engine']}"
    )
    if args.export:
        with open(args.export, "w") as f:
            for a in analyses:
                f.write(json.dumps(episode_record(a)) + "\n")
        print(f"  exported {n} records to {args.export}")
    return 0


def cmd_eval(settings: Settings, args: argparse.Namespace) -> int:
    from pco.analysis.stockfish import StockfishEvaluator
    from pco.decisions.cache import AnalysisCache
    from pco.evaluation.report import EvalConfig, format_report, run_evaluation
    from pco.evaluation.splits import SplitSpec
    from pco.priors.maia3 import Maia3Prior

    conn = connect(settings.db_path)
    if conn.execute("SELECT 1 FROM players WHERE name=?", (args.player,)).fetchone() is None:
        print(f"error: no player named {args.player!r} (see `pco games`)", file=sys.stderr)
        return 1
    config = EvalConfig(
        split=SplitSpec(val_frac=args.val_frac, test_frac=args.test_frac),
        analysis=preset(args.speed).analysis,
        samples=args.samples,
        quality=not args.no_quality,
        seed=args.seed,
    )
    prior = Maia3Prior(
        ensure_asset(settings, MAIA3_5M, offline=True), threads=settings.torch_threads
    )
    sf_path = stockfish_binary(settings)
    workers = args.workers or max(1, min(4, (os.cpu_count() or 2) - 2))
    cache = AnalysisCache(settings.analysis_cache_path)
    print(f"evaluating {args.player} (analysis of val/test decisions is cached)", flush=True)
    try:
        report = run_evaluation(
            conn,
            args.player,
            prior,
            lambda: StockfishEvaluator(sf_path),
            cache,
            config,
            workers,
            args.max_games,
            progress=True,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        cache.close()
        conn.close()
    out = (
        Path(args.out)
        if args.out
        else (
            settings.reports_dir
            / f"eval-{args.player}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
        )
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    print(format_report(report))
    print(f"\nreport: {out}")
    return 0


def main(argv: list[str] | None = None, http: Http | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pco", description="Personal Chess Opponent")
    parser.add_argument("--home", help="data directory (default: $PCO_HOME or platform data dir)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="download and verify model weights and Stockfish")
    sub.add_parser("doctor", help="check installed assets and run a smoke inference")
    games = sub.add_parser("games", help="summarize imported games per player")
    games.add_argument("--verify", action="store_true", help="replay every stored game")

    play = sub.add_parser("play", help="play your Twin in your browser (local)")
    play.add_argument("--port", type=int, default=8765, help="localhost port (0 = any free)")
    play.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="sampling temperature: 1 = model distribution as is, 0 = argmax (default: the "
        "Twin's fitted value, EXPERIMENTS E4)",
    )
    play.add_argument("--seed", type=int, help="random seed for reproducible sampling")
    play.add_argument("--no-browser", action="store_true", help="do not open a browser")
    play.add_argument("--player", help="whose Twin (default: the one built most recently)")
    qs = sub.add_parser("quickstart", help="one command: import your games, train, play")
    qs.add_argument("--chesscom", help="your Chess.com username")
    qs.add_argument("--lichess", help="your Lichess username")
    qs.add_argument("--player", help="local player name (default: the first username)")
    qs.add_argument(
        "--speed", choices=sorted(PRESETS), default=DEFAULT_SPEED, help="analysis budget preset"
    )
    qs.add_argument("--recent", type=int, default=DEFAULT_RECENT_GAMES)
    qs.add_argument("--all", action="store_true", help="use your whole game history")
    qs.add_argument("--workers", type=int, help="parallel Stockfish processes")
    qs.add_argument("--play", action="store_true", help="open the board when training is done")
    qs.add_argument("--no-browser", action="store_true", help="with --play: do not open a browser")
    train = sub.add_parser("train", help="train a personal profile from a player's games")
    train.add_argument("player", help="local player name (see `pco games`)")
    train.add_argument(
        "--speed", choices=sorted(PRESETS), default=DEFAULT_SPEED, help="analysis budget preset"
    )
    train.add_argument("--workers", type=int, help="parallel Stockfish processes")
    train.add_argument(
        "--recent",
        type=int,
        default=DEFAULT_RECENT_GAMES,
        help=f"train on your N most recent games (default {DEFAULT_RECENT_GAMES}; older games"
        " did not improve prediction, EXPERIMENTS E7.1)",
    )
    train.add_argument("--all", action="store_true", help="use your whole game history")

    build = sub.add_parser("build", help="analyze a player's decisions (cached, resumable)")
    build.add_argument("player", help="local player name (see `pco games`)")
    build.add_argument(
        "--speed", choices=sorted(PRESETS), default=DEFAULT_SPEED, help="analysis budget preset"
    )
    build.add_argument("--max-games", type=int, help="only the first N games chronologically")
    build.add_argument("--k", type=int, help="override: max prior candidates per decision")
    build.add_argument(
        "--mass", type=float, help="override: prior mass that stops adding candidates"
    )
    build.add_argument("--nodes", type=int, help="override: engine nodes for the candidate set")
    build.add_argument("--best-nodes", type=int, help="override: engine nodes for best-move search")
    build.add_argument(
        "--workers", type=int, help="parallel Stockfish processes (default: cores-2, max 4)"
    )
    build.add_argument("--export", help="write one JSON record per decision to this file")

    ev = sub.add_parser("eval", help="evaluate move predictors on held-out future games")
    ev.add_argument("player", help="local player name (see `pco games`)")
    ev.add_argument(
        "--speed", choices=sorted(PRESETS), default=DEFAULT_SPEED, help="analysis budget preset"
    )
    ev.add_argument("--max-games", type=int, help="only the first N games chronologically")
    ev.add_argument("--val-frac", type=float, default=0.1, help="validation share of games")
    ev.add_argument("--test-frac", type=float, default=0.2, help="test share (most recent games)")
    ev.add_argument("--samples", type=int, default=2, help="sampled moves per position (quality)")
    ev.add_argument("--no-quality", action="store_true", help="skip the sampled-quality sweep")
    ev.add_argument("--seed", type=int, default=0)
    ev.add_argument("--workers", type=int, help="parallel Stockfish processes")
    ev.add_argument("--out", help="report path (default: $PCO_HOME/reports/eval-*.json)")

    imp = sub.add_parser("import", help="import games (safe to repeat)").add_subparsers(
        dest="source", required=True
    )
    pgn = imp.add_parser("pgn", help="import a PGN file (any number of games)")
    pgn.add_argument("file")
    pgn.add_argument("--username", help="your name in this file; links you to these games")
    pgn.add_argument("--player", help="local player name to link to (default: username)")
    for name, label in (("lichess", "Lichess"), ("chesscom", "Chess.com")):
        p = imp.add_parser(name, help=f"import a {label} account's public games")
        p.add_argument("username")
        p.add_argument("--player", help="local player name to link to (default: username)")
        p.add_argument("--full", action="store_true", help="refetch all games, not just new ones")
        if name == "lichess":
            p.add_argument("--max", type=int, help="maximum number of games to fetch")

    args = parser.parse_args(argv)
    settings = load_settings(args.home)
    try:
        if args.command == "import":
            return cmd_import(settings, args, http or UrllibHttp())
        if args.command == "games":
            return cmd_games(settings, args.verify)
        if args.command == "play":
            return cmd_play(settings, args)
        if args.command == "build":
            return cmd_build(settings, args)
        if args.command == "eval":
            return cmd_eval(settings, args)
        if args.command == "train":
            return cmd_train(settings, args)
        if args.command == "quickstart":
            return cmd_quickstart(settings, args, http or UrllibHttp())
        return {"setup": cmd_setup, "doctor": cmd_doctor}[args.command](settings)
    except sqlite3.DatabaseError as exc:
        print(
            f"error: the game database {settings.db_path} is unreadable ({exc}). It holds your"
            " imported games, so pco will not modify it: restore a backup, or move it away and"
            " re-import (platform imports are repeatable).",
            file=sys.stderr,
        )
        return 1
    except (AssetError, SchemaError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
