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
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import chess

from pco.assets import MAIA3_5M, AssetError, ensure_asset, stockfish_asset, stockfish_binary
from pco.config import Settings, load_settings
from pco.importers.http import Http, UrllibHttp
from pco.importers.pipeline import ImportReport, import_records
from pco.importers.sources import SourceError, chesscom_records, lichess_records, pgn_records
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


def cmd_doctor(settings: Settings) -> int:
    from pco.analysis.stockfish import AnalysisLimit, StockfishEvaluator
    from pco.priors.base import PriorRequest
    from pco.priors.maia3 import Maia3Prior

    print(f"home: {settings.home}")
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


# Lichess `since` filters on game creation, so games still running during the previous
# import started before it; a margin re-fetches them (duplicates are free). Correspondence
# games longer than the margin need --full.
_LICHESS_SINCE_MARGIN_MS = 24 * 3600 * 1000


def _lichess_since_ms(last_run: str | None) -> int | None:
    if not last_run:
        return None
    t = datetime.strptime(last_run, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return int(t.timestamp() * 1000) - _LICHESS_SINCE_MARGIN_MS


def cmd_import(settings: Settings, args: argparse.Namespace, http: Http) -> int:
    conn = connect(settings.db_path)
    try:
        if args.source == "pgn":
            records = pgn_records(Path(args.file))
            locator, username = str(Path(args.file).resolve()), args.username
        else:
            platform = args.source
            username, locator = args.username, args.username.lower()
            # Fail fast on an ownership conflict; the account itself is linked by the
            # pipeline only once games actually arrive (no dangling accounts on errors).
            owner = repo.account_owner(conn, platform, username)
            if args.player and owner and owner != args.player:
                raise repo.AccountConflict(f"{platform} account {username!r} belongs to {owner!r}")
            last = None if args.full else repo.last_successful_run(conn, platform, locator)
            if platform == "lichess":
                records = lichess_records(
                    http,
                    username,
                    since_ms=_lichess_since_ms(last),
                    max_games=args.max,
                    token=settings.lichess_token,
                )
            else:  # chess.com archives are monthly: re-read the month of the last import
                records = chesscom_records(http, username, since_month=last and last[:7])
            if last:
                print(f"incremental: new games since last import ({last}); --full refetches all")
        report = import_records(
            conn,
            args.source,
            locator,
            records,
            username=username,
            player=args.player,
            complete=getattr(args, "max", None) is None,
        )
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
    import webbrowser

    from pco.play.opponent import DEFAULT_PLAY_TEMPERATURE, PriorOpponent
    from pco.play.server import PlayApp, make_server
    from pco.priors.maia3 import Maia3Prior

    prior = Maia3Prior(
        ensure_asset(settings, MAIA3_5M, offline=True), threads=settings.torch_threads
    )
    rng = random.Random(args.seed)
    app = PlayApp(
        lambda elo, temp: PriorOpponent(prior, elo, temperature=temp, rng=rng),
        settings.played_dir,
        elo=args.elo,
        temperature=DEFAULT_PLAY_TEMPERATURE if args.temperature is None else args.temperature,
    )
    try:
        server = make_server(app, args.port)
    except OSError as exc:
        print(f"error: cannot listen on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"pco play: {url}  (opponent: {prior.model_id}, generic - not personalized yet)")
    print("Ctrl-C to stop.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.inference.shutdown()
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
    config = AnalysisConfig(
        k_max=args.k, mass=args.mass, nodes=args.nodes, best_nodes=args.best_nodes
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
    from pco.decisions.analysis import AnalysisConfig
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
        analysis=AnalysisConfig(),
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

    play = sub.add_parser("play", help="play against the opponent in your browser (local)")
    play.add_argument("--port", type=int, default=8765, help="localhost port (0 = any free)")
    play.add_argument("--elo", type=int, default=1500, help="opponent rating for the prior")
    play.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="sampling temperature: 1 = model distribution as is, 0 = argmax "
        "(default 0.7: matched real human move quality, EXPERIMENTS E2.3)",
    )
    play.add_argument("--seed", type=int, help="random seed for reproducible sampling")
    play.add_argument("--no-browser", action="store_true", help="do not open a browser")

    build = sub.add_parser("build", help="analyze a player's decisions (cached, resumable)")
    build.add_argument("player", help="local player name (see `pco games`)")
    build.add_argument("--max-games", type=int, help="only the first N games chronologically")
    build.add_argument("--k", type=int, default=5, help="max prior candidates per decision")
    build.add_argument(
        "--mass", type=float, default=0.95, help="prior mass that stops adding candidates"
    )
    build.add_argument(
        "--nodes", type=int, default=10_000, help="engine nodes for the candidate set"
    )
    build.add_argument(
        "--best-nodes", type=int, default=5_000, help="engine nodes for best-move search"
    )
    build.add_argument(
        "--workers", type=int, help="parallel Stockfish processes (default: cores-2, max 4)"
    )
    build.add_argument("--export", help="write one JSON record per decision to this file")

    ev = sub.add_parser("eval", help="evaluate move predictors on held-out future games")
    ev.add_argument("player", help="local player name (see `pco games`)")
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
        return {"setup": cmd_setup, "doctor": cmd_doctor}[args.command](settings)
    except (AssetError, SchemaError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
