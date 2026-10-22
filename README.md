# Personal Chess Opponent (pco)

Import your chess games → learn how you play → play against yourself.

A local-first, CPU-only chess opponent that approximates *your* move-selection
distribution: a pretrained human-move prior (Maia-3) plus a lightweight learned
personalization layer, with Stockfish used only as an objective evaluator.

**Status:** Iteration 1 is complete: games can be imported and stored. Play and
personalization don't exist yet; see `AI_CONTEXTS/ROADMAP.md`.

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

## Licensing note

Maia-3 (CSSLab) is AGPL-3.0, and this project imports it in-process. Stockfish is
GPL-3.0 and runs as a separate process. The project license has not been chosen
yet; see `AI_CONTEXTS/PROJECT.md`.

Engineering context for contributors and coding agents lives in `AI_CONTEXTS/`.
Start with `HANDOFF.md`.
