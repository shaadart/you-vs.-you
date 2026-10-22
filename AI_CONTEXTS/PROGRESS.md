# PROGRESS LOG

Newest last. Facts only: what changed, what was run, and what the results were.

## 2026-10-23: Iteration 0 (foundation and feasibility): COMPLETED

**Starting state.** The directory was empty: no files, no git. The machine has
Python 3.14 (system), uv 0.11.21, git and node. Stockfish and lc0 are not installed.

**Research**
- Maia-1 (`CSSLab/maia-chess`): GPL-3.0, lc0 `.pb.gz` weights, needs the lc0 binary.
  Rejected: an extra native binary, plus the Maia-2/3 accuracy results.
- Maia-2 (`maia2` 0.11.0 on PyPI): MIT. Needs Python <3.13 and torch 2.8, and the
  weights come from Google Drive via gdown (267 MiB each, rapid and blitz).
- Maia-3 (`CSSLab/maia3`, ICLR 2026 "Chessformer"): AGPL-3.0, PyTorch transformer.
  Weights on HF `UofTCSSLab/Maia3-{3M,5M,23M,79M}`. Takes an 8-position history and
  a continuous Elo. Checkpoints are trained without clock input. There are no
  tagged releases, so it is pinned to commit `1e13597`.
- Stockfish 19 (`sf_19`, released 2026-09-05) has a universal Linux x86-64 build.
- Platform APIs were probed (EXPERIMENTS E0.4). Chess.com is OK. The Lichess
  user-games export returned 404 anonymously.

**Experiments run** (all results in EXPERIMENTS.md)
- E0.1: prior comparison on 26,825 positions. Maia-3 5M has top-1 0.561 / NLL 1.325
  against Maia-2 rapid at 0.513 / 1.493 and uniform NLL 3.187.
- E0.2: Maia-3 5M throughput. Forward-bound; about 10 ms per position live and
  about 190 positions/s at batch 64 with 4 threads.
- E0.3: Stockfish candidate scoring. 18 ms per position at 10k nodes (K=5).

**Files added**
- `pyproject.toml`, `uv.lock`, `.python-version` (3.12), `.gitignore`, `README.md`,
  `CLAUDE.md`.
- `docs/MASTER_SPEC.md`: the master prompt, verbatim.
- `src/pco/`: `config.py`, `assets.py`, `cli.py`, `priors/base.py`,
  `priors/maia3.py`, `analysis/stockfish.py`.
- `tests/`: `conftest.py`, `test_foundation.py` (8 offline tests), and
  `test_assets_integration.py` (6 tests against real assets; they skip if the
  assets are absent).
- `scripts/`: `feasibility_priors.py`, `feasibility_stockfish.py`,
  `fetch_feasibility_data.sh`.
- `AI_CONTEXTS/*`: all six files.

**Validation (final run)**
- `uv run pytest`: 14 passed with assets installed. With an empty `PCO_HOME`,
  8 passed and 6 skipped.
- `uv run ruff check .` and `ruff format --check .`: clean. `uv lock --check`: OK.
- `uv run pco setup`: both assets downloaded and their SHA-256 verified, under
  `~/.local/share/pco/assets`.
- `uv run pco doctor`: prior inference 12 ms (warm). Stockfish 19 scored 3
  candidates at 20k nodes in 36 ms. With an empty home it reports both assets
  missing and exits 1.

**Bugs found during the iteration**
- The hatchling build failed without `README.md` (fixed by adding it).
- A test assertion had an `and`/`or` precedence bug that made it vacuous (fixed
  before the first run).
- An early hypothesis that Python tokenization limits throughput was disproved by
  profiling (E0.2). The docs were corrected.

**Not done or open**
- The project license was not chosen (AGPL implication; see PROJECT.md).
- The cause of the Lichess 404 is unknown, and nothing was tested with a token.
- No git commit was made. The repo was initialized with `git init`, and every file
  is untracked.

## 2026-10-23: Iteration 1 (game import and data foundation): COMPLETED

**Files added**
- `src/pco/storage/`: `db.py` (connect + migrations), `migrations.py` (schema v1),
  `games.py` (repository, summary, verify).
- `src/pco/importers/`: `normalize.py`, `sources.py`, `pipeline.py`, `http.py`.
- `tests/test_normalize.py` (31 tests), `tests/test_import.py` (22 tests).
- `tests/fixtures/`: `games.pgn` (8 synthetic games covering Lichess, chess.com,
  OTB with variations and NAGs, from-position, Chess960, an illegal move, a missing
  name, and a daily game); `lichess_user.ndjson`, the `chesscom_*.json` files, and
  `build_fixtures.py` that generates them. All players are fictional.

**Files changed**
- `config.py`: added `lichess_token` (`PCO_LICHESS_TOKEN`).
- `cli.py`: added `import {pgn,lichess,chesscom}` and `games [--verify]`.
- `pyproject.toml`: E501 exemption for `tests/fixtures/*`.

**Validation (final run)**
- `uv run pytest -q`: 67 passed with assets installed, and 59 passed + 6 skipped
  with an empty `PCO_HOME`. ruff check and format are clean, and
  `uv lock --check` passes.
- Live functional runs are recorded in EXPERIMENTS E1.1: 4,666 real games, 0
  rejected, 0 replay failures, and cross-source dedup confirmed.
- `pco doctor` still passes.

**Bugs found and fixed during the iteration**
1. A non-object JSON record (e.g. `[]`) raised `AttributeError` and would have
   aborted an import. It is now a rejection.
2. The incremental cutoff was taken from the newest stored game for that username,
   so a PGN import made a later account import skip older archive months. It now
   uses the last complete, successful import run of that source and account.
3. Repeat imports with different username case (`alice` vs `Alice`) hit an
   account conflict. An existing account now keeps its owner when no `--player`
   is given.
4. A `--max` run counted as an incremental base, which would have hidden older
   games forever. Limited runs now have `complete=0`.
5. A failed import of a nonexistent user left an empty player and account behind.
   Accounts are now linked only when games arrive.
6. Lichess `TimeControl "14 days per move"` was unclassified, and Lichess ignores
   `max` together with `perfType` (both found on live data).
7. python-chess logged illegal-move errors to stderr during imports. A quiet
   builder now collects them as rejections.
8. An early `skip_game` call after `read_headers` would have skipped every other
   game. It was caught by reading the docs before the first run (`read_headers`
   already consumes the movetext). The 312/312 split count confirms it.

**Decisions:** ARCHITECTURE D7–D11.

**Notes**
- Schema v1 was edited in place during this iteration (the `import_runs.complete`
  column). That is safe because no database outside this session's scratch
  directories existed. From now on, schema changes must be new migrations.
- E0.4 was superseded: the Lichess 404 came from curl's default User-Agent, not an
  auth requirement.
