# HANDOFF

_Last updated: 2026-10-24, end of Iteration 7 (all roadmap iterations complete)._

## State

- **Iterations 0–7: COMPLETED.** The product works end to end on Linux x86-64 CPU:
  `pco quickstart --chesscom U [--lichess U] [--play]`, or the individual
  commands `import` → `train` → `play` → `eval`.
- Defaults:
  - speed preset **lite** (same accuracy as standard/deep, about 2× faster; E7.2)
  - training on the **2,000 most recent games** (E7.1)
  - recency half-life chosen on val
  - play temperature fitted per player
  - play at 1 torch thread
- `uv run pytest -q` passes 133. ruff is clean.
- **The owner's own data lives in the real `~/.local/share/pco`:**
  - player `shaadpgn` (chesscom:shaadpgn + lichess:keerkeet), 9,496 games
  - a trained profile (Standard speed, 2,000 recent games)

  Never modify or delete it. The analysis cache there is derived and safe to
  rebuild. Don't record personal statistics in `AI_CONTEXTS/`.
- Research scratch homes (h8/h9/h10/h12/h13 under the session scratchpad) are
  disposable public-data copies.

## If continuing

There is no pending roadmap iteration. Candidates, all measurement-first (ROADMAP
"Future research"):
1. Per-platform Elo offsets (for players mixing Chess.com and Lichess).
2. Clocked play, so learned time-pressure behavior can appear live.
3. Richer residual models, judged out-of-book with paired CIs on the 3 reference
   players. The splits are fixed by fingerprint: thibault `c3a0cae2ad7e9b7b`,
   warpentak `a9726602c78de4a9`, LonelySheperd `ce02dba7162fb9df`.
4. macOS and Windows verification.

## Owner decisions pending

- **The project license** (AGPL-compatible if distributed with Maia-3; PROJECT.md).
- Iterations 5–7 are uncommitted in git (the owner commits when ready).

## Watch out for

- Context feature groups failed a pre-registered cross-player test (E6.2). Don't
  enable them by default without a new pre-registered test.
- Formats: prior cache key `k2`, profile format 2, PERSONAL_FEATURE_VERSION 2,
  main DB schema v1, CACHE_SCHEMA 1.
- Stockfish can refuse odd positions. That is handled per position
  (`engine_error`).
- zsh doesn't word-split variables. Use scripts with explicit arguments.
- Python is pinned to 3.12. Always use `uv run`.

## Commands

```sh
uv sync && uv run pco doctor
uv run pco quickstart --chesscom USER [--lichess USER] [--speed lite|standard|deep] [--play]
uv run pco import chesscom USER && uv run pco train NAME      # incremental update
uv run pco play --player NAME
uv run pco eval NAME [--speed lite]
uv run pytest -q && uv run ruff check . && uv run ruff format --check .
```
