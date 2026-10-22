# HANDOFF

_Last updated: 2026-10-23, end of Iteration 1._

## State

- **Iterations 0 and 1: COMPLETED.** **Next: Iteration 2, playable chess core and
  human prior (PENDING, not started).**
- Last working state:
  - `uv sync`, then `uv run pco setup` installs Maia-3 5M and Stockfish 19.
  - `pco doctor` runs a smoke test.
  - `pco import pgn|lichess|chesscom ...` imports games into `$PCO_HOME/pco.sqlite3`.
  - `pco games [--verify]` summarizes the stored games and replays all of them.
  - `uv run pytest -q` passes 67 of 67.
- What exists: config, assets, the `HumanPrior`/`Maia3Prior` and
  `StockfishEvaluator` adapters, schema v1 storage, the import pipeline with three
  adapters, and the CLI. Nothing exists yet for play, decisions, evaluation or
  personalization.

## Read next

1. ROADMAP.md, "2 — Playable core and human prior": scope and the UI proposal.
2. ARCHITECTURE.md, "Key interface contracts" (`HumanPrior`, the planned
   `Opponent`) and D5.
3. `src/pco/priors/base.py` and `src/pco/priors/maia3.py`. The prior already
   returns a full legal distribution and supports temperature sampling.

## Exact next actions (Iteration 2)

1. **Decide the UI at the start:** a localhost single-page board (stdlib
   `http.server`, inline SVG, no CDN, works offline) or a terminal UI. The
   recommendation is the localhost page, plus a headless game-session API that
   tests drive directly.
2. `src/pco/play/`: a `GameSession` (python-chess board + history, legality,
   termination including claimable draws, resignation, PGN export of played games
   with a clear "generated" marker; never import them as human evidence) and an
   `Opponent` protocol with `PriorOpponent(prior, elo_self, elo_oppo,
   temperature, rng)`.
3. Error handling: catch prior failures and present them explicitly. Never
   substitute an engine move silently. A fallback must be labeled as one.
4. A CLI command such as `pco play [--elo N] [--color white|black] [--seed S]`.
5. Measure live latency and RSS during real games, and record them as EXPERIMENTS
   E2.x.

## Watch out for

- Pass the real move stack to the prior. History is worth +1.9 points top-1 (E0.1).
- Games produced by play must never enter `games` as human evidence (spec rule).
  If they are stored, give them a separate table or kind.
- Schema changes must now be new migrations (v2+). Don't edit v1.
- Python is pinned to 3.12. Always use `uv run`.
- The project license is undecided (AGPL via Maia-3). Ask the owner before any
  public distribution.

## Commands

```sh
uv sync && uv run pco setup && uv run pco doctor
uv run pco import pgn FILE --username NAME [--player ME]
uv run pco import lichess USER [--max N] [--full]     # PCO_LICHESS_TOKEN optional
uv run pco import chesscom USER [--full]
uv run pco games --verify
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```
