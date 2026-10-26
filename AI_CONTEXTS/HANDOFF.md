# HANDOFF

_Last updated: 2026-10-26, after the "Symphony" design refinement (all roadmap iterations complete)._

## State

- **Iterations 0–7: COMPLETED.** The product works end to end on Linux x86-64 CPU:
  `pco quickstart --chesscom U [--lichess U] [--play]`, or the individual
  commands `import` → `train` → `play` → `eval`.
- **The interface** (`pco play`) is the product: home → meet a version of
  yourself → game with whispers → reflection / difference / replay, plus Archive,
  Twin (portrait) and Settings (import, rebuild, depth, forget). Spec:
  `docs/DESIGN.md` (Mirror identity + Twin experience, with Geist Pixel titles, the
  home glimpse, and time/continuity). Decisions: ARCHITECTURE D37–D48.
- Defaults:
  - speed preset **lite** (same accuracy as standard/deep, about 2× faster; E7.2)
  - training on the **2,000 most recent games** (E7.1)
  - recency half-life chosen on val
  - play temperature fitted per player
  - play at 1 torch thread
- `uv run pytest -q` passes 148. ruff is clean. `node tests/js/ui_check.js` passes.
- **The owner's own data lives in the real `~/.local/share/pco`:**
  - player `shaadpgn` (chesscom:shaadpgn + lichess:keerkeet)
  - The owner reset and rebuilt through the app on 2026-10-26: a cold Quick build
    (1,843 s), then a cached rebuild. Their preference is Quick, 4 workers. Those two
    profiles predate per-build cache counts and `stage_seconds` (D45 handles them).

  Never modify or delete it (no imports or forgetting on it without the owner).
  The analysis cache there is derived and safe to rebuild. Don't record personal
  statistics in `AI_CONTEXTS/`.
- Research scratch homes under the session scratchpad are disposable public-data
  copies.

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
- The "Twin" UX iteration is uncommitted in git (the owner commits when ready).

## Watch out for

- Time estimates (D44/D45) are only as good as the measured costs in
  `play/library.py` `STAGE_COST` (E8.1, one 12-core machine). The live pace takes
  over within seconds of each counted stage; re-measure if the pipeline changes.
- Never run CPU-bound Python threads beside live analysis (D48).

- An old `pco play` (pre-Twin code) holds the analysis cache's write lock until it
  exits. The new app copes (D43), but CLI `pco train` would wait 30 s and then fail
  while such a process is open. Ask the owner to restart old servers.
- The page's visible copy must never call the Twin an AI, bot, engine or computer
  (`test_page_never_calls_the_twin_a_machine` and `ui_check.js`).

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
uv run pco play                     # the app; --player NAME to choose
uv run pco eval NAME [--speed lite]
uv run pytest -q && uv run ruff check . && uv run ruff format --check .
```
