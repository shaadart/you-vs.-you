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

## 2026-10-23: Iteration 5 (personalized move selection V1): COMPLETED

**Starting state.** The owner committed Iterations 2–4 (`5b4b5f5`) and reported
that the opponent was playing better at T=0.7.

**Files added**
- `src/pco/personal/`: `features.py`, `model.py`, `predictor.py`, `profile.py`,
  `opponent.py`, `__init__.py`.
- `tests/test_personal.py` (9 tests):
  - gradient and Hessian vs finite differences, plus convergence
  - w=0 equals the base, normalization, non-candidate proportions
  - recovery of a planted preference (held-out ΔNLL < −0.5)
  - no overfitting to noise
  - features independent of the actual move
  - profile round-trip and version guards
  - live == offline to 1e-9
  - a complete game vs the PersonalOpponent
  - prior-mismatch rejection

**Files changed**
- `evaluation/report.py`: personal predictors, train-split analysis (memoized),
  out-of-book bootstrap CIs, paired `vs_baseline` comparisons, and an
  engine-unavailable count.
- `evaluation/predictors.py`: `book_blend()` extracted. EloOffset raises on a
  prior mismatch (It. 4).
- `decisions/episodes.py`: `live_episode()`.
- `decisions/analysis.py`: `EpisodeAnalysis.engine_error` and `.forced`. The
  builder survives `UnsupportedPosition`.
- `decisions/features.py`: `forced` and `engine_error` in the context.
- `analysis/stockfish.py`: `EngineExited` and `UnsupportedPosition`, with
  restart-on-EOF.
- `play/opponent.py`: `MoveChoice.likeness`.
- `play/server.py` and `index.html`: likeness display and personal labeling; the
  Elo input is disabled for personal opponents.
- `config.py`: `profiles_dir`.
- `cli.py`: `pco train`, `pco play --player`.
- `scripts/bench_play.py`: `--player`.
- Tests: `test_evaluation.py` (new predictor set), `test_decisions.py` (engine
  failure doesn't abort a build), `test_assets_integration.py` (real Stockfish
  restarts after an unsupported position).

**Validation**
- `uv run pytest -q`: 120 passed. With an empty home, 108 passed and 12 skipped.
  ruff is clean and the lock is OK. `pco doctor` passes.
- Real data (EXPERIMENTS E5.1/E5.2):
  - `pco eval` on both players, 1,000 games each, on the same splits as E4.
  - `pco train thibault` (996 games, 431 s).
  - `pco play --player thibault` served and played through the API, with a
    headless-Firefox screenshot of the personal UI.
  - Full-game latency benchmark: p50 ≈50 ms.

**Bugs found and fixed**
1. Live vs offline distributions differed by 4e-7. Live called `prior.predict`
   directly, while training used canonical 6-decimal values. Live now uses
   `prior_distributions`. Found by the exactness test.
2. Stockfish exits on a seven-queen "From Position" start (real data). One
   position aborted a 7-minute build. Engine failures are now per-position, with
   restart.
3. A race: the EOF arrived before the exit status was readable, so the first
   restart check missed it. EOF is now the death signal.
4. `engine is None` conflated forced moves and engine failures. They are now
   separate (`forced`, `engine_error`).
5. Two background evaluations once wrote the same log concurrently, from a
   duplicate launch caused by a zsh word-splitting mistake in the agent's shell
   command. The final reported runs were rerun cleanly and sequentially.

**Decisions:** ARCHITECTURE D25–D28.

## 2026-10-23: Iteration 6 (context, temporal behavior, incremental learning): COMPLETED

**Owner direction:** keep it optimized, fast and easy to train, and aim for a bot
that plays in the same style as any given player. The owner also confirmed that
the T=0.7 opponent played better.

**Files added**
- `src/pco/personal/drift.py`.
- `scripts/ablate_personal.py`.
- Tests in `tests/test_personal.py`:
  - weighted objective grad/Hessian and warm start
  - FeatureSpec expansion and v1 compatibility
  - recency weights
  - live == offline with context groups
  - planted drift detected, stable player not flagged across 3 seeds,
    insufficient data
- Tests elsewhere: Elo warm-start (`test_evaluation.py`), and exact,
  history-sensitive prior keys shared by episodes and live boards
  (`test_assets_integration.py`).

**Files changed**
- `personal/features.py`: base + context + `GROUPS`/`FeatureSpec`, version 2.
- `personal/model.py`: weighted objective, spec expansion, warm start.
- `personal/predictor.py`: joint λ × half-life selection on val, default
  half-lives {none, 400, 200, 100, 50}.
- `personal/profile.py`: format 2 (groups, half-life, drift, cache stats); `previous`
  warm start.
- `personal/opponent.py`: spec.
- `evaluation/predictors.py`: coarse-to-fine and warm-start Elo search.
- `priors/maia3.py`: `input_key` format k2.
- `decisions/cache.py`: the prior-key format is tracked; only the prior table is
  cleared on change.
- `decisions/episodes.py`: unchecked pushes, memoized boards.
- `cli.py`: `pco train` prints work, recency and drift, and warm-starts from the
  last profile.

**Validation**
- `uv run pytest -q`: 127 passed. With an empty home, 114 passed and 13 skipped.
  ruff is clean and the lock is OK.
- Real data:
  - Ablations on 3 players (one new: LonelySheperd, 1,000 games, imported this
    iteration).
  - The pre-registered confirmation failed for context features (E6.2).
  - `pco eval` of the deployable configuration on 3 players (E6.3).
  - Incremental training measured on a cold cache (E6.4).
  - A result-neutrality check of the speedups reproduced metrics to 1e-9.
  - Live play with a format-2 profile: p50 54.5 ms.

**Bugs found and fixed**
1. Drift detection's first test produced a false positive: it bootstrapped over
   about 4 validation games. It now needs ≥30 validation games, and the test was
   rebuilt on a properly sized sampled population.
2. Earlier tests assumed the fixed 13-point Elo grid. They were updated for the
   coarse-to-fine search (7 passes, offset 500 reachable).

**Negative results kept** (spec: record unsuccessful approaches): time-control,
clock-band and opponent-rating interactions showed no gain on any of the 3
players. Phase interactions helped 1 of 3, and the pre-registered v2 failed on the
independent player.

**Decisions:** ARCHITECTURE D29–D31.

## 2026-10-24: Owner's own profile trained (between It. 6 and It. 7)

- The owner imported their own accounts (Chess.com plus Lichess, linked to one
  local player) into the real `$PCO_HOME`: about 9.5k games and 280k own
  decisions over 5 years. Personal data stays local, and no stats about the owner
  are recorded here.
- **Added `pco train --recent N`**: train on only the N most recent dated games
  (fast, current form). Tested in `test_personal.py`. With `--recent 2000` (about
  60k decisions) a cold training run took 2,360 s on 4 engine workers (≈44
  decisions/s analysis with the machine also busy). A warm retrain took 157 s.
- **Bug fixed: the Elo-offset search could stop on the edge of its range.** The
  owner's profile fitted +500, which is the coarse-to-fine maximum. Real cause:
  low Chess.com ratings sit far below Maia's Lichess scale. Both the cold and warm
  searches now keep stepping outward while the edge improves, up to
  `MAX_ELO_OFFSET` = ±1200. After the fix, the warm retrain evaluated +600, found
  it worse, and confirmed +500 as an interior optimum. Tests were updated:
  `uv run pytest -q` passes 128.
- Open: per-platform rating offsets. A single offset serves both Chess.com and
  Lichess games. That's fine when one platform dominates, unmeasured otherwise.
  This belongs in It. 7.

## 2026-10-24: Iteration 7 (product integration, optimization, release readiness): COMPLETED

**Owner input:** the owner plays mostly on Chess.com, was concerned about a
1.5-hour full-history training, and likes the opening predictions.

**Files added**
- `src/pco/presets.py`.
- `scripts/book_history.py`.
- Tests in `test_foundation.py` (presets, quickstart validation, doctor summary),
  `test_decisions.py` (corrupt cache rebuilt), and `test_import.py` (corrupt DB
  untouched).

**Files changed**
- `cli.py`:
  - `quickstart`
  - `--speed` on build, train and eval
  - `train` defaults to `--recent 2000`, with `--all`
  - interrupt handling and a DB-corruption message
  - the doctor data summary
  - play at `live_threads`
- `decisions/cache.py`: quarantine and rebuild of a corrupt cache.
- `config.py`: `live_threads`.
- `evaluation/predictors.py`: the Elo search never stops on a range edge (±1200).
- `personal/profile.py`: `recent_games`.
- `README.md`: rewritten for release.

**Validation**
- `uv run pytest -q`: 133 passed. ruff is clean.
- Real runs:
  - The E7.1 book experiment.
  - Preset evals: Lite on 3 players, Deep on 1 (E7.2).
  - A fresh-home quickstart (E7.3).
  - The owner's profile trained and retrained (previous entry).

**Bugs found and fixed**
1. The quickstart decision count was quadratic: it rebuilt a sorted set per
   episode. Fixed before running.
2. The quickstart time estimate covered only analysis and underestimated by about
   1.7×. It now uses the measured total-training factor (0.6).
3. A test assertion about which Elo offsets get evaluated was wrong: the refine
   step evaluates both neighbors. Corrected.
4. Earlier in this session: the Elo search was capped at the grid edge (it found
   +500 for the owner). Fixed and verified interior.

**Decisions:** ARCHITECTURE D32–D36.

## 2026-10-26: Interface iteration "Mirror" (owner request: premium, minimal, human/machine duality)

- Spec: `docs/DESIGN.md` covers principles, layout, interactions, typography and a
  palette with WCAG ratios (every text token ≥ 5.0:1 in both themes, computed),
  plus accessibility and third-party assets.
- `src/pco/play/static/index.html` rewritten:
  - Paper and Graphite themes.
  - Bundled Newsreader (human voice) and IBM Plex Mono (machine voice).
  - cburnett SVG pieces, replacing Unicode glyphs that depended on system fonts.
  - Pointer drag with a ghost origin, click, and typed SAN/UCI moves (`/`).
  - An on-board promotion chooser.
  - A probability spectrum with hover ghost arrows; the played move is always
    shown, even when it is an outlier.
  - The last move tinted by owner (ochre for the human, cobalt for the machine).
  - Uncertainty-paced replies.
  - A native `<dialog>` for new games (color incl. random, temperature showing the
    fitted value).
  - Two-step resign, inline errors (no `alert()`), shortcuts (`/ N F Esc ?`),
    synthesized sound with a remembered toggle, reduced-motion support, and a
    mobile single column with a sticky action bar.
- `play/server.py`: a whitelisted `/static/fonts/*.woff2` route (traversal and
  other files 404), `legal` with SAN, structured opponent fields, and
  per-response Cache-Control.
- Verified:
  - `node tests/js/ui_check.js` exercises 11 flows on the real page script.
  - `uv run pytest -q`: 136 passed (3 new server tests).
  - Headless-Firefox screenshots at 1440×900 in light and dark, and at 390×844 on
    a live personal-opponent server.
- Bugs caught by the screenshots:
  1. The `hidden` attribute was overridden by `display` styles, so "Retry" showed
     during play. Fixed with a global `[hidden]` rule.
  2. Latency was uppercased ("MS").
  3. An outlier sampled move (p < 0.005) displayed as "p 0.00" and was missing
     from the spectrum.
  The suspected square-color inversion was ruled out by sampling pixels.

## 2026-10-26: UX iteration "Twin" (owner request: the experience of playing yourself)

- Spec: `docs/DESIGN.md` now has two layers. **Mirror** (visual identity: tokens,
  fonts, board) is unchanged and is the source of truth. **Twin** is the experience
  built on it: I → my games → my Twin → the game → the reflection, with PLAY ·
  ARCHIVE · TWIN · SETTINGS. The opponent is always "you" / "your Twin".
- New modules:
  - `play/twin.py` (TwinService): versions, observations, whispers, reflection.
  - `play/library.py` (Library): players, games, Twins, preferences, imports,
    builds, forgetting.
  - `play/jobs.py` (JobRunner): one background job, stage + machine fragments.
  - `importers/accounts.py` (`import_account`): shared by the CLI and the app.
- Changed:
  - `play/server.py` rewritten around the Twin. Routes: `/api/home`, `versions`,
    `twin`, `archive`, `settings` (GET/POST), `job`, `reflection`, `import`,
    `build`, `forget` (typed confirmation), `player`, plus the game routes.
    Unexpected errors return JSON 500 instead of dropping the connection.
  - `pco play` opens the app (picks the player with the newest Twin; onboarding
    when there are no games). `--elo` and the generic-prior opponent left the
    interface.
  - `train_profile(report=...)` reports the build stages. `DecisionBuilder.analyze`
    accepts a progress callback. `import_records(progress=...)`.
  - Profiles record `weight_cov` (D40). `PersonalOpponent.analyze()` returns the
    distribution plus its engine analysis (D41).
  - `AnalysisCache(busy_timeout, best_effort)` and `clear()` (D43).
  - `pgn_text_records` for uploaded PGN text.
  - The page is rewritten as a single page with hash routes, keeping every Mirror
    token.
- Verified:
  - `uv run pytest -q`: 144 passed (new: `tests/test_twin.py`, the onboarding →
    import → build → play → reflection → forget flow over HTTP, a visible-copy
    test). ruff is clean. `node tests/js/ui_check.js` covers the routing, the
    ritual, optimistic moves, whispers, the reflection copy, the difference and
    replay boards, and build stages.
  - Real run on the owner's home: the Twin was rebuilt through `/api/build` with
    every stage reported, fully cached, in 114 s. A new profile (with
    `weight_cov`) was saved; older profiles are kept. Live analysis of a human move
    and the Twin's reply each took about 0.13–0.15 s at Lite (4 moves; informal).
  - Headless-Firefox screenshots of every screen at 1440×900 (light) and 500×758
    (dark; Firefox's minimum headless width).
- Bugs caught by the real run and screenshots:
  1. Time-control versions pooled every year (a 2021-heavy "Bullet You"). They now
     use the past 365 days.
  2. The machine voice uppercased SAN ("1.D4"). Notation now keeps its case.
  3. The portrait printed `[object Object]` (a list where a count was expected), and
     its machine fragments inherited the 24px serif.
  4. "You have made this kind of move 40,023 times" was true but meaningless. The
     difference view now counts your moves in that exact position, and otherwise
     uses a finer kind (piece, character, phase, destination).
  5. Whispers fired one move apart. All kinds are now spaced ≥ 8 plies, and
     "unusual" means < 5% of the top choice (it was 8%).
  6. The reflection could say "It knew you well." above "That wasn't quite you."
     The loss line now follows the match.
  7. **"database is locked":** an older `pco play` process (pre-change code, which
     commits only on exit) held the analysis cache's write lock. The new server's
     move failed with an uncaught error. Fixed by D43 and the JSON 500 handler.
  8. With a short busy timeout, a *locked* cache could have been mistaken for a
     corrupt one and moved aside. It is now re-raised instead.
- Not done, recorded:
  - The spec's "N similar decisions found → Compare them" step is realized as
    position-level counts in "See the difference", not as a search over similar
    past positions.
  - There are no clocks, so "time" in game is elapsed time.
  - Versions are approximations (D39).

## 2026-10-26: Design refinement "Symphony" (owner request: refine, don't reinvent)

- **Geist Pixel for titles only** (Square, OFL, npm `geist` 1.8.2, 28 KB, bundled with
  its license). It sets the home question, "YOU WON.", "SHOW ME YOUR CHESS.", the
  portrait, archive years, build status and the ritual's second YOU (your Twin, in
  pixels, under you in the serif). Every sentence stays in Newsreader or Plex Mono.
  The portrait's echo became a faint serif YOU behind the pixel one. D47.
- **Home shows instead of telling.**
  - A real position from your recent games that you have faced often; answer it and
    see what you usually play there and what your Twin would play (two arrows, the
    distribution in the machine voice).
  - First visit: "Play against yourself." Your first move is compared with players
    near 1500 (the human model), then `SHOW ME YOUR CHESS`. The home no longer
    redirects to import.
  - New `/api/glimpse` (GET a position, POST an answer).
- **Time made understandable** (D44, D45).
  - Stage fractions come from real counts. `Job.remaining()` uses the live pace or a
    plan, and the plan rests on the last build, this machine's calibrated pace, or
    the measured presets ("roughly").
  - Every start button shows its cost first (`ABOUT 1 H 10 MIN · EVERY GAME IS READ
    AGAIN AT THE NEW DEPTH`).
  - The build screen lists every stage: done ones with their times, the current
    one's count, and the rest faint.
  - First-time note; "You can leave this page."
  - Twins now save `stage_seconds` and per-build cache counts.
- **Continuity** (D46).
  - Starting work while it runs joins it (`joined: true`, was a 409 "already
    working" dead end).
  - One job watcher feeds a quiet top-bar indicator, Home and Settings. A finished
    job turns the indicator ochre ("YOUR TWIN IS READY").
- Verified:
  - `uv run pytest -q`: 148 passed. ruff is clean. `node tests/js/ui_check.js` passes
    (25 requests: glimpse in both modes, first-visit home, joining, the indicator,
    time words, the build screen).
  - Real runs on copies of homes (E8.1): a cached rebuild through the page (estimate
    about 2 min, actual 1:36) and a first build (planned 1,830 s, actual 1,909 s).
  - Headless-Firefox screenshots: desktop light, mobile dark.
- Bugs caught by real runs:
  1. **The shared cache's lifetime totals were saved as a build's own work.** The
     owner's 156 s rebuild claimed 137,977 computed engine searches. Training now
     records deltas; older profiles are read with a plausibility guard.
  2. **A first glimpse answer took 8 s, then 5 s.** A CPU-bound Python thread
     (decision-kind counts at startup) convoyed the model's interpreter-lock
     handoffs. Kind counts moved to the reflection, over the Twin's 2,000-game window
     (D48).
  3. **The machine suspended for 61 minutes during the first-build test.**
     Wall-clock timing made the live estimate 5,807 s right after waking. Job timing
     is now monotonic.
  4. **The Thorough estimate first said 38 min.** It used another laptop's preset
     speed; calibration from this machine's own cold build gives 42 min. A
     depth-change re-read now reuses cached model results, as it really does.
  5. The empty fragments block left a gap above "MEET YOURSELF". The glimpse's
     machine line inherited the serif size.
- **The owner's own home** was only read: the analysis of their two Twins and the
  plans for each depth. They had done a full forget → import → cold build (1,843 s)
  → rebuild themselves earlier that afternoon.
