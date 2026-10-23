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

## 2026-10-23: Iteration 2 (playable chess core and human prior): COMPLETED

**Starting state.** The owner had committed Iterations 0–1 as `8cecdc8`. An
uncommitted owner change to `.gitignore` (ignoring `CLAUDE.md`) was left untouched.
67 tests passed.

**Files added**
- `src/pco/play/`: `opponent.py`, `session.py`, `server.py`, `static/index.html`,
  `__init__.py`.
- Empty `__init__.py` for `storage/` and `importers/` (these were implicit
  namespace packages; behavior unchanged).
- `tests/test_play.py` (17 tests: session rules, mate, threefold, promotion,
  resignation, opponent failure, generated-PGN refusal, HTTP game, security
  checks, and 2 real-Maia tests). `tests/test_ui_js.py` and `tests/js/ui_check.js`
  run the page script against DOM stubs, and skip without node.
- `scripts/bench_play.py`.

**Files changed**
- `normalize.py`: rejects `[PcoGenerated "true"]` games.
- `config.py`: added `played_dir`.
- `cli.py`: added `pco play [--port --elo --temperature --seed --no-browser]`.

**Validation (final run)**
- `uv run pytest -q`: 85 passed (assets and node present). ruff check and format
  are clean.
- Manual functional run: `pco play --no-browser --port 8799 --seed 1` against real
  Maia. Four human moves through the API got legal, plausible replies with
  probabilities and top-5 lists. A foreign `Host` header gets 403. A headless
  Firefox screenshot shows the correct position, last-move highlight, decision
  panel and move list.
- Node UI check: 64 clickable squares, target markers, a move sent and followed
  by an opponent request, and a promotion picker that sends `a7a8n` only after a
  piece is chosen.
- E2.1 and E2.2 measurements, under external load (see EXPERIMENTS).

**Bugs found during the iteration**
- Two test-authoring errors: a FEN that put the king in check, and wrong-color
  moves in the seed test. Both were fixed in the tests; the code was correct.
- `pco play` output was block-buffered when stdout is not a TTY, so the ready
  line didn't appear. Fixed with `flush=True`.
- Per-request threads rebuilt torch thread pools, costing about 10 ms per move.
  Fixed with a persistent inference worker.

**Decisions:** ARCHITECTURE D12–D15.

**Open**
- Latency must be re-measured on an idle machine; the E2.1 numbers were taken at
  load average 14–16.
- License still undecided.

## 2026-10-23: Post-Iteration-2 investigation (owner report: opponent too weak)

- The owner played `pco play` (Maia-3 5M, Elo 1500, T=1) and found it made more
  mistakes than expected and seemed weaker than a Maia they had played before.
- Verified the adapter is correct (top-1 0.572 on 8k positions, matching E0.1),
  then measured strength vs real humans with the new `scripts/strength_check.py`.
  Result (E2.3): T=1 sampling has 32–37% more cp loss and 38–49% more blunders than
  real 1300–1700 players on the same positions. Argmax is stronger than those
  humans. T≈0.6–0.7 matches them, while NLL is best at T≈0.9–1.
- Changed: `DEFAULT_PLAY_TEMPERATURE = 0.7` (`play/opponent.py`), used by
  `PriorOpponent`, `PlayApp`, `pco play` (where `--temperature` defaults to it) and
  the page form. ARCHITECTURE D16. ROADMAP It. 4 and It. 5 now require
  sampled-quality-match metrics and a fitted play temperature.
- Re-measured live latency on an idle machine (E2.1b): p50 20.2 ms at 4 threads and
  13.5 ms at 1 thread.
- Tests: 85 passed. ruff is clean.

## 2026-10-23: Iteration 3 (decision episodes, features, analysis): COMPLETED

**Files added**
- `src/pco/decisions/`: `episodes.py`, `cache.py`, `analysis.py`, `features.py`,
  `__init__.py`.
- `tests/test_decisions.py`: 12 tests covering own-move coverage, exact pre-move
  positions, clock semantics, leakage, forward-only Elo carry, missing clocks,
  adaptive candidates, a live set unaffected by the actual move, cache hits and
  invalidation, cache schema, forced moves, flagged labels with a label-free
  live view, and a real-asset reproducibility test.

**Files changed**
- `analysis/stockfish.py`: rewritten as a minimal UCI driver, with an `Evaluator`
  protocol, `best()`, 4 MB default hash, an engine_id that includes settings, and
  `EngineError`.
- `priors/base.py` and `maia3.py`: `input_key()`; `model_id` is now
  `maia3-5m@<sha256[:12]>`.
- `config.py`: `analysis_cache_path`.
- `cli.py`: `pco build`.
- `scripts/strength_check.py`: uses `best()`.
- `scripts/feasibility_stockfish.py`: pins 64 MB to reproduce E0.3.
- `tests/test_foundation.py`: parser test.
- `tests/test_assets_integration.py`: driver ≡ python-chess, plus driver error
  handling.

**Validation (final run)**
- `uv run pytest -q`: 100 passed. With an empty home, 89 passed and 11 skipped.
  ruff is clean and the lock is OK.
- Live: `pco build thibault` on 200 real games (7,281 decisions) took 126 s cold
  and 4.9 s cached (E3.1).
- Exports were byte-identical across a fresh-cache rebuild with different worker
  counts, and across cached vs fresh builds.
- `pco doctor` evaluations are identical to the old driver (d2d4 +36, f1b5 +30,
  f1c4 +18).

**Bugs found and fixed**
1. A first build vs a cached rebuild differed in prior floats: the cache rounds
   to 6 decimals but fresh results didn't. Both paths now go through
   `canonical_logprobs`.
2. The first driver version dropped `lowerbound`/`upperbound` lines. That gave
   an older score than python-chess for an interrupted slot, and lost candidates
   entirely in short searches (a crash in the reproducibility test). Bounds are
   now kept like python-chess, and `evaluate` raises `EngineError` if any
   candidate is still missing.
3. The builder's worker closure captured loop variables (ruff B023; latent bug).
   It was refactored into explicit methods.
4. Test-authoring errors: the "forced move" FEN was actually checkmate. Fixed.
5. Process-pool experiments hung on shutdown (python-chess non-daemon thread).
   Abandoned; see E3.2. During cleanup, `pkill -f` twice killed the agent's own
   shell, because the pattern matched its command line. Only scratch processes
   were affected.

**Decisions:** ARCHITECTURE D17–D20.

## 2026-10-23: Iteration 4 (evaluation framework and baselines): COMPLETED

**Files added**
- `src/pco/evaluation/`: `splits.py`, `predictors.py`, `metrics.py`,
  `quality.py`, `report.py`, `__init__.py`.
- `tests/test_evaluation.py` (9 tests on a synthetic player with a fixed opening
  repertoire, plus an undated game and a duplicated game). They cover:
  chronological game-level splits with duplicate drop and fingerprint stability;
  tie-aware top-k and full-distribution NLL, with truncation rejected; ECE values;
  bootstrap CI behavior; temperature normalization; Elo-offset selection, with ties
  preferring 0 and a prior-mismatch guard; book gains in-book only;
  reproducibility of the quality sweep; and an end-to-end report that is
  deterministic, with `fit` never seeing test games (spy predictor).
- `tests/fakes.py`: shared test doubles moved out of `test_decisions.py`.

**Files changed**
- `decisions/analysis.py`: `cached_search()` was factored out of `analyze_engine`
  (behavior unchanged) and reused for scoring sampled moves.
- `config.py`: `reports_dir`.
- `cli.py`: `pco eval`.
- README: an evaluation section.

**Validation**
- `uv run pytest -q`: 109 passed. ruff is clean.
- Real runs:
  - `pco eval thibault`: 1,000 games, 816 s cold.
  - `pco eval warpentak`: 1,000 games, 1,152 s cold.
  - The thibault cached rerun took 150 s, with identical metrics, parameters,
    quality and split.
  - Results are in EXPERIMENTS E4.1/E4.2.

**Bugs found and fixed during the iteration**
1. My first top-k rule credited ties to the actual move, which would have scored
   uniform at 100% top-1. It now uses the expected hit under random tie-breaking.
2. Grid ties picked the first grid value (Elo offset −600 for a rating-insensitive
   prior). Ties now prefer no change (offset 0, T 1).
3. `EloOffset` silently mixed distributions when the analyses came from a
   different prior. It now raises (caught by a test).
4. Test-authoring issues were fixed before landing: a mutation of shared fixture
   episodes was replaced with `dataclasses.replace`, and a vacuous assertion was
   removed.
5. The first background launch used `env time`, which is not installed. It was
   relaunched with shell timing.

**Decisions:** ARCHITECTURE D21–D24; D16 updated with per-player temperature
evidence.
