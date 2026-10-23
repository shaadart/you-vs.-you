# Personal Chess Opponent (pco)

Import your chess games → learn how you play → play against yourself.

A local-first, CPU-only chess opponent that approximates *your* move-selection
distribution: a pretrained human-move prior (Maia-3) plus a lightweight learned
personalization layer, with Stockfish used only as an objective evaluator.

**Status:** Iteration 4 is complete. You can import games, play a *generic*
human-move model (Maia-3) in your browser, analyze your decisions, and measure how
well move predictors anticipate your *future* games. Personalization doesn't exist
yet; see `AI_CONTEXTS/ROADMAP.md`.

## Setup

Requires [uv](https://docs.astral.sh/uv/). uv installs Python 3.12 and CPU-only PyTorch.

```sh
uv sync                 # create .venv with all dependencies
uv run pco setup        # download and verify Maia-3 weights (21 MB) and Stockfish 19
uv run pco doctor       # smoke-test the prior and the engine
uv run pytest           # tests; asset-dependent tests skip if `pco setup` was not run
```

## Importing your games

Imports are safe to repeat. Games already stored are skipped, and account imports
fetch only what's new since the last complete import.

```sh
uv run pco import pgn my_games.pgn --username MyName   # any PGN, from any source
uv run pco import lichess MyLichessName                 # public games via the Lichess API
uv run pco import chesscom MyChesscomName               # public games via the Chess.com API
uv run pco games --verify                               # summary, plus a replay check of every game
```

Use `--player Me` on each import to link several accounts to one person. Imported
data stays local in `$PCO_HOME/pco.sqlite3`. Only standard chess is imported;
Chess960 and other variants are skipped, and the reason is reported.

All local state (assets, database, caches) lives under `$PCO_HOME`, which defaults to
`~/.local/share/pco` on Linux. Other variables are `PCO_STOCKFISH` (path to an
existing Stockfish binary), `PCO_TORCH_THREADS`, and `PCO_LICHESS_TOKEN` (optional).

## Playing

```sh
uv run pco play                       # opens http://127.0.0.1:8765/ in your browser
uv run pco play --elo 1800 --seed 7   # opponent rating; a fixed seed makes sampling reproducible
```

The opponent samples moves from Maia-3's human-move distribution at the chosen
rating. The default temperature is 0.7. At temperature 1 it measurably blunders more
often than real players of that rating, while 0.7 matched their move quality (see
`AI_CONTEXTS/EXPERIMENTS.md`, E2.3). At 0 it always plays its most likely move,
like the Lichess Maia bots. It is not yet personalized to you. The server listens on 127.0.0.1 only. Finished games are
saved to `$PCO_HOME/played/` and tagged as generated, so they are never imported
as your games.

## Analyzing your decisions

```sh
uv run pco build MyName                    # every one of your own moves: Maia-3 probabilities
                                           # plus Stockfish scores of the plausible alternatives
uv run pco build MyName --export dec.jsonl # one JSON record per decision, for inspection
```

The build is cached in `$PCO_HOME/cache/`. Rerunning, or resuming after Ctrl-C,
only computes what is missing, and the cache can be deleted at any time. On a
10-core laptop it runs at roughly 60 decisions per second.

## Measuring how well a model predicts you

```sh
uv run pco eval MyName      # trains on your older games, tests on your newest 20%
```

This prints a table and saves a JSON report under `$PCO_HOME/reports/`.

- Each model is scored on your held-out games: how often it predicts your actual
  move (top-1/top-3), the log-likelihood of your actual move, and calibration.
  Confidence intervals come from resampling whole games.
- Opening positions you have played before are reported separately ("in-book"),
  so memorized repertoire is not mistaken for understanding your style.
- The sampled-quality check compares the engine loss of moves *sampled* from each
  model with your actual moves, and picks the sampling temperature that plays at
  your level.

## Licensing note

Maia-3 (CSSLab) is AGPL-3.0, and this project imports it in-process. Stockfish is
GPL-3.0 and runs as a separate process. The project license has not been chosen
yet; see `AI_CONTEXTS/PROJECT.md`.

Engineering context for contributors and coding agents lives in `AI_CONTEXTS/`.
Start with `HANDOFF.md`.
