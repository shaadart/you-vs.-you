# ARCHITECTURE

Legend: **[impl]** exists in the repo and is tested; **[planned]** is designed only.

## Stack (decided in Iteration 0)

| Concern | Choice | Why |
|---|---|---|
| Language/runtime | Python 3.12, uv-managed (`.python-version`, `uv.lock`) | The ML ecosystem and python-chess are Python. The upper bound comes from maia2/torch 2.8 wheels. |
| Chess rules / PGN / UCI | `chess` (python-chess) 1.11 | Mature legality, PGN parsing and UCI engine driver |
| Inference | PyTorch 2.8 **CPU wheels** (`pytorch-cpu` uv index; default PyPI on macOS) | Maia-3 is a PyTorch model; CPU wheels avoid CUDA bloat |
| Human prior | `maia3` @ git `1e13597` + HF weights `UofTCSSLab/Maia3-5M@b6559de` | Best accuracy per byte and latency measured (EXPERIMENTS E0.1) |
| Evaluator | Stockfish 19 via UCI subprocess | Objective candidate evaluation. A separate process keeps the GPL boundary and allows crash isolation. |
| Storage | SQLite (stdlib `sqlite3`), one file under `$PCO_HOME` | Local, zero-ops, transactional, queryable |
| CLI | stdlib `argparse` (`pco` entry point) | Minimal dependencies |
| Tests / lint | pytest, ruff | — |

No web framework, ORM, or task queue. Revisit only with a measured need.

## Components and interfaces

```
src/pco/
  config.py           [impl] Settings: home, assets_dir, db_path, cache_dir, stockfish override, torch threads
  assets.py           [impl] Pinned Asset manifest; download -> sha256 verify -> atomic install; offline mode
  priors/base.py      [impl] HumanPrior protocol, PriorRequest, MoveDistribution (UCI -> logprob over ALL legal moves)
  priors/maia3.py     [impl] Maia3Prior: in-process CPU inference, 8-position history from board.move_stack
  analysis/stockfish.py [impl] Evaluator protocol; StockfishEvaluator = minimal UCI driver: evaluate(candidates), best()
  presets.py          [impl] speed presets lite/standard/deep (AnalysisConfig budgets), DEFAULT_SPEED=lite,
                             DEFAULT_RECENT_GAMES=2000
  cli.py              [impl] `pco quickstart | setup | doctor | import {pgn,lichess,chesscom} | games [--verify] |
                             play [--player] | build | eval | train [--speed] [--recent N|--all]`
  storage/db.py       [impl] connect() (WAL, foreign keys) + ordered migrations via schema_version
  storage/migrations.py [impl] schema v1 (raw + normalized layers)
  storage/games.py    [impl] repository: runs, raw records, games, accounts, summary, verify
  importers/sources.py  [impl] adapters -> RawRecord(kind, text): pgn file split, Lichess NDJSON, chess.com archives
  importers/normalize.py [impl] RawRecord -> NormalizedGame (one PGN path for all sources)
  importers/pipeline.py  [impl] import_records(): raw store -> normalize -> dedup insert -> account linking
  importers/accounts.py  [impl] import_account(): incremental Chess.com/Lichess import shared by CLI and interface
  importers/http.py   [impl] stdlib HTTP client: User-Agent, 429/5xx retry, injectable
  play/opponent.py    [impl] Opponent protocol, MoveChoice, PriorOpponent (samples the prior; no fallback)
  play/session.py     [impl] GameSession: turns, legality, termination, resignation, generated-PGN export
  play/server.py      [impl] PlayApp (one game + the Twin + jobs) + stdlib ThreadingHTTPServer on 127.0.0.1 (JSON API)
  play/twin.py        [impl] TwinService: versions (year/time-control books + median ratings), observations (z-gated
                             weight contrasts), whispers, post-game reflection (per-decision Twin view + divergences)
  play/library.py     [impl] Library: the interface's view of $PCO_HOME (players, games, Twins, preferences, imports
                             via import_account/import_records, builds via train_profile, forgetting)
  play/jobs.py        [impl] JobRunner: one background job at a time; (stage, fragment, fraction) reports;
                             time left from live pace or the plan (D44)
  play/static/index.html [impl] single page, "Mirror" identity + "Twin" experience (docs/DESIGN.md): hash routes for
                             home/meet/game/reflection/review/twin/archive/settings/import/build
  play/static/fonts/  [impl] bundled Newsreader + IBM Plex Mono woff2 (OFL), served by a whitelisted /static route
  decisions/episodes.py [impl] DecisionEpisode (observed, pre-move only) + player_episodes() chronological
  decisions/cache.py  [impl] AnalysisCache: separate SQLite file under cache_dir (prior + engine results)
  decisions/analysis.py [impl] AnalysisConfig, prior_candidates (adaptive), analyze_engine (shared live/offline),
                             prior_distributions (batched+cached), DecisionBuilder (pipelined, pooled engines)
  decisions/features.py [impl] context/candidate features + episode_record() (FEATURE_VERSION=1)
  evaluation/splits.py  [impl] chronological game-level split (train/val/test), undated excluded,
                             duplicate move sequences dropped from held-out, fingerprint
  evaluation/predictors.py [impl] Predictor protocol (fit on train+val, predict full legal dists):
                             Uniform, GenericPrior, EloOffset, Temperature, Book (composable)
  evaluation/metrics.py [impl] NLL/top-k (tie-aware)/ECE+reliability, breakdowns, game-cluster bootstrap
  evaluation/quality.py [impl] sampled-quality match (engine loss of sampled vs actual moves) per temperature
  evaluation/report.py  [impl] run_evaluation() -> JSON report; format_report()
  personal/features.py [impl] BASE_FEATURES (23) + CONTEXT (8) + interaction GROUPS; FeatureSpec(groups)
                             (PERSONAL_FEATURE_VERSION=2); featurize() -> Block (live set only)
  personal/model.py   [impl] residual conditional logit: stack(spec, weights), objective() (weighted NLL+L2,
                             grad, Hessian), fit_weights() (Newton + Armijo, warm start), distribution()
  personal/predictor.py [impl] Residual(base, analyze, spec, half_lives): Predictor; lambda + recency
                             half-life chosen jointly on val NLL (warm-started lambda path)
  personal/drift.py   [impl] behavior_drift(): older-half vs newer-half model on recent games (bootstrap CI)
  personal/profile.py [impl] PersonalProfile (JSON artifact) + train_profile() (90/10 chronological)
  personal/opponent.py [impl] PersonalOpponent: live prior -> live set -> engine -> residual -> book -> sample
```

Key interface contracts:
- **HumanPrior.predict(requests) → MoveDistribution[]** is batched. A distribution
  is keyed by UCI from the real board's perspective, covers every legal move, and
  its probabilities sum to 1. It raises `PriorError` when there are no legal moves.
  Rating context is a required `self_elo` and `oppo_elo`. Policy for missing
  ratings is decided upstream (It. 3/4: fitted Elo or the player's median).
- **StockfishEvaluator.evaluate** scores a given candidate set using `root_moves` and
  multipv, from the mover's perspective, with mates mapped to ±(10000 − n). It is
  node-limited, single-threaded and sends `ucinewgame` per call, so results are
  deterministic and cacheable (verified by test).
- **Source adapters** yield `RawRecord(kind, text)` with no interpretation. Kinds:
  `pgn` (one game, sliced byte-exact from the file), `lichess-ndjson` (one line
  exactly as streamed), and `chesscom-json` (one archive game re-serialized with
  sorted keys: lossless but not byte-exact).
- **normalize(kind, text) → NormalizedGame** or `NormalizationError`. It is a pure
  function and never touches the database. Unknown metadata stays `None`.
- **import_records(conn, source, locator, records, username, player, complete)**
  is idempotent and commits in batches of 200. On any exception, including Ctrl-C,
  it keeps committed progress, marks the run `failed`, and re-raises.
- **Opponent.choose_move(board) → MoveChoice** [impl]. A `MoveChoice` holds the
  chosen move, its probability, the top 5, the source model id, and latency. The
  board passed in carries its move stack, so priors read their own history from
  it. `PriorOpponent` exists now; `PersonalOpponent` comes in It. 5. The UI only
  sees `label`, which must say truthfully what the opponent is ("not
  personalized" for now). Failures raise `OpponentError` and the position is left
  unchanged.
- **HumanPrior.input_key(request)** [impl]: a deterministic string naming every
  input the output depends on. For Maia-3 that's the 8-position history FENs plus
  both Elos. `model_id` is architecture + weight SHA-256 (`maia3-5m@ba14208b2992`).
  Together they are the prior cache key.
- **DecisionEpisode** [impl] holds observed inputs only:
  - the game, its chronological index, ply, color, platform, date, time control
  - resolved Elos with their source (`game` / `carried` from an *earlier* game /
    `default`)
  - the mover's clock *before* the move, the opponent's clock, and the previous
    move's think time
  - `root_fen` + up to 7 `recent_moves` (rebuilds the board with prior history)
  - `actual_move`, which is a label and never an input
- **analyze_engine(evaluator, cache, fen, prior_cands, config, actual=None)** [impl]
  is the single path for offline and live use:
  1. An unrestricted best-move search at `best_nodes`.
  2. One shared search over (prior candidates ∪ engine best) at `nodes`: the *live
     set*.
  3. Training only: if the actual move is outside the live set, a separate
     single-move search, so the live set's scores are identical to live
     inference.
- **episode_record(analysis, include_label)** [impl]: the training/eval example. Its
  context features carry missing-data flags. Candidates are the live set plus,
  with a label only, the actual move flagged `in_live_set=False` and
  `separately_evaluated`.
- **Predictor.fit(train_episodes, val_analyses) / predict(analyses) → [logprobs over
  ALL legal moves]** [impl]. This is the contract that It. 5's personalized model
  must implement to be evaluated. Coverage of every legal move is checked by the
  metrics (truncated distributions raise). `fit` never receives test data, which a
  test asserts with a spy predictor.
- **run_evaluation(conn, player, prior, make_evaluator, cache, EvalConfig)** [impl]:
  1. The split.
  2. Analysis of val+test only (train needs only moves, for the book).
  3. Each predictor is fit and scored on non-forced test decisions.
  4. A bootstrap vs generic Maia.
  5. The sampled-quality sweep, with the temperature chosen on val and reported
     on test.

  Reports are deterministic except the `timing` block (test).
- **PersonalOpponent.distribution(board)** [impl] runs exactly the training pipeline:
  1. Prior at the profile rating, through `prior_distributions` (canonical
     precision).
  2. `prior_candidates` and `analyze_engine` give the live set.
  3. The base is the prior at rating + offset.
  4. `featurize`, then the residual `distribution`.
  5. The book blend.

  A test asserts the live distribution equals the offline `Residual.predict` to
  1e-9. `choose_move` samples at `profile.play_temperature` and reports
  `likeness` = P(move)/P(top). If the engine refuses the position, it falls back
  to prior + book for that move, and that is noted in `MoveChoice.source`.
- **Play HTTP API** [impl]: `GET /`, `GET /api/state`, `GET /api/pgn`, and
  POST `/api/new {color, elo, temperature}`, `/api/move {uci}`, `/api/opponent`,
  `/api/resign`. The human move and the opponent reply are separate requests, so
  the page shows the human move at once. Errors return 409 with the current state.

## Data flow (implemented through It. 5)

```
PGN / Lichess / Chess.com ──importer──> raw_games (byte-exact) ──normalize──> games
games ──(player's own moves only)──> decision episodes ──prior + Stockfish (cached)──> features
features (chronological train split) ──train──> personal profile artifact (versioned)
live play: board+history ──prior──> candidates ──Stockfish──> features ──profile──> P(move) ──sample──> move
```

## Storage (SQLite, `$PCO_HOME/pco.sqlite3`)

The layers follow the spec. Raw data is immutable apart from its `normalize_error`
diagnostic. Higher layers can be rebuilt from lower ones. The exact DDL is in
`src/pco/storage/migrations.py`.

1. **Raw [impl, schema v1]**
   - `import_runs`: source, locator (file path or lowercased username), timestamps,
     status (running/ok/failed), counts, and a `complete` flag (0 for `--max`
     runs). Incremental imports start from the last `ok` + `complete` run.
   - `raw_games`: kind, `content_sha256 UNIQUE`, raw_text, first run, fetched_at,
     and `normalize_error`. Rejected records are kept here too, so they can be
     re-normalized after a fix.
2. **Normalized games [impl, schema v1]**
   - `games`: `dedup_key UNIQUE`, platform, external_id, played_at, players, Elos,
     result, termination, tc base/inc, time_class, rated, start/final FEN,
     `moves_uci` (space-separated, mainline), n_plies, `clocks_s` (JSON per ply or
     NULL), and normalizer_version.
     It points at the first raw record that produced it. Later raw copies of the
     same game are stored but don't create a second game.
   - `players(name UNIQUE)` and `accounts(player_id, platform, username,
     UNIQUE(platform, username_lower))`. One person can own several accounts.
   - View `player_games` resolves color by joining accounts on (platform,
     lowercased name). Color is not stored redundantly.
3. **Decision data [impl, It. 3]**
   - Episodes are *not* stored. They are recomputed from `games` (cheap and
     deterministic).
   - Expensive analysis lives in a **separate file**,
     `$PCO_HOME/cache/analysis.sqlite3` (`AnalysisCache`, own `CACHE_SCHEMA`; a
     version mismatch clears it). Deleting it never touches evidence:
     - `prior_cache(key, model_id, logprobs zlib-JSON at 6 decimals)`, key =
       sha256(model_id, input_key)
     - `engine_cache(key, engine_id, nodes, evals JSON)`, key = sha256(engine_id
       incl. threads and hash, mode best|set, nodes, FEN, sorted candidates)
   - Features are recomputed from the caches on read, versioned by
     `FEATURE_VERSION`. About 1 KB of cache per decision (E3.1).
4. **Derived [It. 5+]**
   - **[impl, It. 5] Profiles are JSON files only**, at
     `$PCO_HOME/profiles/<player>/profile-<UTC stamp>.json`. There is no DB table
     yet (see D25). The newest one is loaded. A profile contains:
     - weights, feature names and version
     - prior id and engine id
     - the analysis config
     - Elo and Elo offset
     - lambda and book alpha
     - the book counts (position → move counts)
     - the play temperature
     - a data fingerprint
     - validation metrics, including the quality sweep

     It is about 2 MB per 1,000 games, mostly the book.

Versioning: a `schema_version` table with ordered SQL migrations. Every cache key
includes the producer's id and version, so changing a model invalidates its cache
rows instead of silently mixing them.

## Configuration

Environment variables or CLI flags; no config file yet. The variables are
`PCO_HOME` (default `~/.local/share/pco`, macOS `~/Library/Application Support/pco`,
Windows `%LOCALAPPDATA%\pco`), `PCO_STOCKFISH`, and `PCO_TORCH_THREADS` (default
min(4, cores)), and `PCO_LICHESS_TOKEN` (optional personal token: higher rate
limit, never required). Planned: a hardware profile (It. 7).

## Decisions and their reasons

- **D1 Maia-3 5M over Maia-2 and Maia-1.** Measured to be higher in accuracy, lower
  in NLL and 13x smaller than Maia-2, with no lc0 binary needed (unlike Maia-1).
  Evidence: EXPERIMENTS E0.1. The 23M variant is a candidate "Deep" profile.
- **D2 Maia-3 in-process via the upstream package, pinned by commit.** Tokenization
  stays identical to training. Cost: an AGPL obligation (see PROJECT.md) and a
  dependency on a young repo with no releases. Mitigation: the adapter uses only five
  upstream symbols, so vendoring them is easy.
- **D3 Own asset manager instead of the HF hub cache or gdown.** It gives one
  versioned location, offline-first behavior, SHA-256 pinning, and no extra
  network library.
- **D4 Stockfish limits are node-based, not time-based.** This gives reproducible
  evaluations that can be cached and shared between training and live inference.
  Time budgets are applied only by choosing the node count.
- **D5 The prior returns the full legal distribution.** Personalization adjusts a
  candidate subset and leaves the rest at prior mass, so probability coverage is
  always complete. This avoids the candidate-truncation evaluation problem.
- **D6 Maia-3 checkpoints ignore clock input** (`include_time_info=False`). All
  time and clock conditioning must therefore live in the personalization layer.
- **D7 One normalization path.** Every source goes through `normalize_pgn`. The
  JSON adapters only override fields the platform states explicitly (id, rated).
  Rules are identical for file and API imports, so cross-source dedup works.
- **D8 Dedup key = `platform:external_id`, else a content hash.** The platform and
  id are recovered from PGN headers (Lichess `GameId`/Site, chess.com `Link`), so a
  game exported as a file and later fetched by API is stored once (verified on live
  data, E1.1). Games without an id (OTB) dedup by players + date + start FEN +
  moves.
- **D9 `time_class` is derived by one rule for every platform** (Lichess
  thresholds on base + 40 × increment; daily or `-` controls count as
  correspondence). Platform labels disagree, and a single rule keeps time-control
  conditioning comparable across accounts.
- **D10 Accounts are platform-scoped**, and they are linked only when a game with
  that name actually arrives. Same-named people on different platforms are not
  merged unless the user names the same `--player`. A failed import never leaves
  a dangling account.
- **D11 Lichess is fetched as NDJSON with `pgnInJson=true`.** It gives per-game
  records on a stream (byte-exact raw storage without re-splitting PGN) and filters
  variants server-side with `perfType`. `max` is also enforced client-side (E1.1).
- **D12 Play UI = localhost page on the stdlib HTTP server.** No dependencies,
  works offline, real board interaction, and testable through `PlayApp` and HTTP
  (plus a node DOM-stub check of the page script). It binds to 127.0.0.1 only,
  checks the Host header against DNS rebinding, and requires JSON POSTs, so other
  sites can't drive it with simple form posts.
- **D13 Games end when a draw becomes claimable** (threefold, 50-move), as well as
  on automatic outcomes. A sampling opponent never shuffles forever, and there is
  no claim UI to build.
- **D14 Played games are generated data.** They are saved as PGN files under
  `$PCO_HOME/played/` (never in `games`), tagged `[PcoGenerated "true"]`, and the
  normalizer rejects that tag, so they can't re-enter as human evidence even if
  someone imports them by hand.
- **D15 No move-source fallback.** If the prior fails, the page shows the error
  and a retry. Opponent inference runs on one persistent worker thread, because
  OpenMP pools are per calling thread (E2.2).

- **D16 Play temperature is a measured parameter, not 1.0.** Sampling the prior at
  T=1 was ~40% more blunder-prone than the real humans it models, while T≈0.9–1 is
  NLL-optimal (E2.3). The generic default is 0.7 (`DEFAULT_PLAY_TEMPERATURE`); a
  personal profile will carry its own fitted temperature. Likelihood-optimal ≠
  behavior-matching, so both are evaluated (It. 4). *Update (E4.1/E4.2):* the
  quality-matching T was 0.7 for a ≈1800 player and 1.0 for a ≈1220 player, so a
  per-player fit is required, and 0.7 is only the generic fallback.

- **D17 Decision analysis is cached outside the main DB**, keyed by producer
  identity + settings + exact inputs. It is reproducible: a fresh rebuild with a
  different worker count is byte-identical (E3.1). Fresh and cached prior values
  both pass through `canonical_logprobs` (6 decimals), so a first build equals a
  cached rebuild.
- **D18 The live candidate set never sees the actual move.** Training-only
  evaluation of an out-of-set actual move runs as its own search. The live set is
  adaptive: prior top moves until cumulative mass ≥ 0.95 (max 5, min 2), plus the
  engine's best move. Forced positions skip the engine.
- **D19 Own minimal UCI driver, 4 MB hash, Threads=1.** python-chess's engine layer
  stopped thread scaling, and large-hash clearing per `ucinewgame` was
  memory-bound (E3.2). The driver's output equals python-chess's for identical
  settings (test). Bound lines are kept as the last line per MultiPV slot, like
  python-chess.
- **D20 Engine evaluations are repetition-blind** (bare FEN), so the cache key
  fully determines results. Prior evaluation does see the 8-position history.

- **D21 Evaluation = chronological, game-level, future-predicting.** The oldest
  70% of a player's games train, the next 10% validate (all tuning), and the
  newest 20% test. Forced moves are excluded from headline metrics. Undated games
  are excluded. Held-out games that duplicate earlier move sequences are dropped.
- **D22 Scores always come from full legal distributions**, so log-likelihood is
  never undefined by candidate truncation (spec §4). Top-k is the expected hit
  under random tie-breaking, so uniform gets 1/n, not credit for ties.
- **D23 Opening bias is reported, not hidden.** Every metric has an
  in-book/out-of-book split ("position occurred in the player's training games").
  Improvements must be read from the *out-of-book* column to claim generalization.
- **D24 Uncertainty comes from a game-clustered bootstrap** (1,000 resamples of
  test games). Moves within a game are dependent, so a per-move CI would be too
  narrow.

- **D25 Profiles are JSON files, not a DB table** (deviation from the It. 0 plan).
  They are a self-contained, versioned, inspectable artifact, and loading checks
  the format and feature version. A table becomes worthwhile only when
  multi-profile management is needed (It. 6/7).
- **D26 Personalization v1 = residual conditional logit over the live candidate
  set**, with base = Maia at a fitted Elo offset, plus the opening book on top.
  - w = 0 is exactly the base. The objective is convex, so Newton converges in a
    few iterations.
  - A non-candidate actual move contributes through a fixed "rest" term in the
    normalizer.
  - Training artifacts (`separately_evaluated`, `in_live_set`) are never features.
  - λ is chosen on val NLL. The val curve is nearly flat at small λ, and λ=1e-5
    (the grid minimum) was chosen twice (E5.1).
- **D27 Live and offline inference share every step and the canonical prior
  precision.** This was found by an exactness test: a direct `prior.predict` call
  live differed by 4e-7 from the 6-decimal canonical values used in training.
- **D28 Engine failures are per-position.** `StockfishEvaluator` restarts on EOF
  and raises `UnsupportedPosition`. The builder records `engine_error`, and that
  decision gets no engine features, is excluded from scoring, and is counted
  separately from forced moves. `EpisodeAnalysis.forced` now means one legal
  move. Real trigger: a "From Position" game with seven white queens makes
  Stockfish 19 exit.

- **D29 Context conditioning is opt-in, recency is selected per player.** Feature
  groups (time control, clock bands, opponent rating, phase) are expanded
  vectorized from a per-decision context, so variants cost seconds. A
  pre-registered cross-player test failed (E6.2), so the default stays v1
  features. The recency half-life ∈ {none, 400, 200, 100, 50} is chosen on val:
  it helped the player with the longest history and was not selected for the
  others.
- **D30 Behavior-change detection is diagnostic only.** It fits on the older vs
  newer halves of training and scores both on the newest games, with a
  game-clustered CI. It requires ≥30 validation games, because bootstrapping few
  clusters is anti-conservative (caught by a test). It is reported by
  `pco train` and stored in the profile. It never changes the model by itself;
  recency weighting is the mechanism that does.
- **D31 Training-speed decisions (E6.1/E6.3).**
  1. Stored moves are pushed without re-validation; import already verified them.
  2. `DecisionEpisode.board()` is memoized and returns copies.
  3. The Maia-3 `input_key` is the oldest window position's FEN plus the moves
     since (format `k2`). It is equivalent and needs 1 FEN instead of 8. A
     format change clears only the prior cache table, never engine results.
  4. The Elo offset is a coarse-to-fine search: 7 prior passes over val instead
     of 13.

- **D32 Speed presets change only the analysis budget**: k_max, mass and the
  node counts. The model, features and training are identical. A profile stores
  its analysis config, and play reuses it. **Lite is the default**, because all
  three presets gave the same held-out accuracy (≤0.001 NLL) on three players
  while Lite is about 2× faster than Standard and about 4–5× faster than Deep
  (E7.2).
- **D33 Training defaults to the 2,000 most recent games.** A whole-history
  opening book was significantly worse (+0.027 NLL), and a recency-weighted one
  no better (E7.1). The residual already down-weights old games. `--all`
  remains available.
- **D34 `pco quickstart` is a thin orchestrator** over setup → import → train
  (→ play). It reuses the same command functions and estimates total time from
  the preset's measured throughput × 0.6 (E7.3).
- **D35 Recovery policy.**
  - A corrupted *derived* file (the analysis cache) is moved aside automatically
    and rebuilt.
  - A corrupted *evidence* file (the game DB) is never modified: pco explains how
    to restore it or re-import.
  - An interrupted train or build resumes from the cache.
  - Network errors abort with a message, and their partial progress is
    committed.
- **D36 Live play defaults to 1 torch thread** (`PCO_LIVE_THREADS`). That is
  fastest at batch 1 (E2.1b). Training keeps `PCO_TORCH_THREADS`.

- **D37 Interface "Mirror" (docs/DESIGN.md) stays dependency-free and offline.**
  - Fonts are bundled, about 225 KB, served only by a strict `/static/fonts/*.woff2`
    pattern. The pieces are inline SVG symbols, sanitized at import.
  - Two visual voices: human (serif, ochre) and machine (mono, cobalt).
  - The state API adds `legal` (UCI+SAN) for typed moves, and structured opponent
    fields (`name`, `model`, `default_temperature`).
  - The played move always appears in the spectrum, even when it is an outlier.
  - The opponent's reply is paced by 250 ms + 750 ms × (1 − p). This is
    presentation only; the model's latency is still reported.

- **D38 The interface is the product; the opponent is "you".** `pco play` serves
  one app (home, versions, game, reflection, archive, Twin, settings, import,
  build) over a `Library` bound to `$PCO_HOME`. The generic-prior opponent left
  the interface (`PriorOpponent` stays for tests and scripts). Visible copy never
  says AI/bot/engine/computer (tested).
- **D39 Versions are approximations, stated as such.** A version = that slice's
  own opening book (first 40 plies) + its median rating, combined with the current
  residual weights. Years need ≥ 150 games. Time-control versions use the past 365
  days. No per-version training: it would multiply build time, and small slices
  fit noise.
- **D40 Observations require clear evidence.** Profiles record
  `weight_cov` = pinv(Hessian of the mean objective) / Kish n_eff. A sentence is
  shown only for |z| ≥ 4, and the one-hot piece weights are read as contrasts
  against the mean piece. Older profiles without a covariance show opening facts
  only. Covariances are optimistic (moves within a game correlate), hence the
  high threshold.
- **D41 Whispers and reflections reuse live analysis.** Each human move is
  analyzed once by the version's own model (`PersonalOpponent.analyze`, the same
  path as play). That analysis feeds the whisper and is cached per ply for the
  reflection, so the post-game view costs only the extra engine scores of
  out-of-set moves. "Very you" compares the game's top-3 share with the
  profile's own validation top-3.
- **D42 One job at a time, in-process.** Imports and builds run in a daemon
  thread and report (stage, machine fragment); the page polls `/api/job`. Builds
  share the live `AnalysisCache` instance (thread-safe through its lock) and load
  their own Maia at `PCO_TORCH_THREADS`, then reset torch to `PCO_LIVE_THREADS`.
- **D43 Live play never blocks on the cache.** The app opens the analysis cache
  with a 0.1 s busy timeout and best-effort writes, and commits after every move.
  Another pco process holding the write lock costs only uncached results, never a
  stalled or failed move. A locked cache is never treated as corrupt.

- **D44 Time left is computed from real state, never invented.** Work reports
  `(stage, detail, fraction)`. Fractions come from countable work: decisions
  analyzed, the 25 regularization/recency fits, the 6 sweep temperatures, Chess.com
  monthly archives, and Lichess games seen against the account's count (first
  imports only). `Job.remaining()` uses the current stage's own pace once ≥ 3% and
  ≥ 2 s of it are measured ("live"). Otherwise it uses the plan, and it always adds
  the plan for stages still to come. With neither, there is no number.
- **D45 Build plans rest on this machine's history.** `Library.build_plan` prices
  each stage:
  - from the previous Twin's `stage_seconds` when that build was warm at the same
    depth;
  - else from per-decision costs measured here (E8.1);
  - new or re-read decisions at the depth's preset rate, calibrated by the newest
    full (cold) build's own analysis pace.

  Cold vs warm is read from engine searches computed per decision (about 2 cold,
  near 0 warm). Cache hit counts are not clean, because the app's cache is shared
  with live play. The basis is shown: "from your last build", "from this
  machine's past pace", or "roughly" when there is no history.
- **D46 Continuity is server truth.** Starting work while a job runs returns that
  job (`joined: true`) instead of 409. The page has one job watcher (2.5 s, or
  0.8 s on the build screen) feeding the top-bar indicator, Home and Settings. A
  page reload or second tab sees the same job. A server restart ends the job; the
  analysis cache keeps its work.
- **D47 Geist Pixel for titles only** (Square, OFL, 28 KB, bundled). It is the
  machine's signature: the second YOU, headlines and archive years. Every sentence
  stays in Newsreader or Plex Mono.
- **D48 Background Python work must not run beside live analysis.** The model's
  forward pass releases and reacquires the interpreter lock hundreds of times. A
  CPU-bound Python thread beside it turned a 0.1 s answer into 5–8 s (measured).
  Startup warm-up is limited to versions and home positions (~2 s). Decision-kind
  counts are computed by the reflection on the analysis thread.

## Known limitations

- Play: one game at a time per server, no takeback, no clocks or time control.
  Versions other than Current You are approximations (D39). Forgetting deletes
  files under `$PCO_HOME` but cannot recall games already in the platforms'
  public archives.

- Maia-3's encoder uses a perspective-mirrored board history. For a FEN without a
  move stack, history is padded with the current position, which is measurably
  weaker (E0.1).
- Prior throughput is compute-bound on the model forward pass, not tokenization
  (profiled: about 90% forward). Best offline throughput is batch 64: about 190
  positions/s at 4 threads and about 250 at 10 threads. The
  `predict(...)` batch size is the caller's choice; offline builders should use 64.
- Process RSS is about 520 MB with the 5M prior loaded, mostly the torch runtime.
  An ONNX Runtime export is a possible Lite-profile optimization (It. 7, unmeasured).
- Decision building costs about 58 decisions/s (4 engine workers, idle machine;
  E3.1): roughly 20 min for a 70k-decision history, once, then cached.
- Elo conditioning feeds chess.com ratings into Lichess-trained Maia-3 unadjusted.
  Rating pools differ, and a fitted Elo (It. 4 baseline) is the planned remedy.
- Personalization gains are small (≈0.5–1.4% NLL, E5.1/E6). Repertoire (the book)
  is still the dominant player-specific signal. Context conditioning (time
  control, clock, opponent rating) showed no reliable gain on three players
  (E6.1/E6.2), so it is unused by default.
- Live personal play costs ≈50 ms per move (p50, E5.2): prior plus one engine best
  search and one set search. Play has no clocks, so time-pressure features are
  always 0 in live play.
- The book baseline counts train games only. Validation games are not added
  before scoring test, which slightly understates the book on test.
- An evaluation of 1,000 games costs about 14 min cold (analysis of val+test plus
  the Elo grid and the quality sweep), and 2.5 min cached (E4.1).
- Only basic features exist (FEATURE_VERSION 1). No opening-familiarity or
  position-frequency features yet (It. 4 analyzes opening bias first).
- Normalizer changes after release must bump `NORMALIZER_VERSION`. A
  re-normalize command from `raw_games` doesn't exist yet; the raw data keeps it
  possible.
- Incremental Lichess imports re-fetch from 24 h before the last complete run.
  Correspondence games older than that, finishing later, need `--full`.
- Chess960 and other variants are rejected (stored raw, with a reason). Standard
  games starting from a custom FEN are accepted.
- Tested only on Linux x86-64. macOS and Windows should work: pure Python, a
  CPU torch wheel, and Stockfish from PATH or `PCO_STOCKFISH`. That is
  unverified.
- One Elo offset per player, even with accounts on several platforms with
  different rating scales. That is fine when one platform dominates; otherwise
  it is unmeasured.
- Only the Linux x86-64 Stockfish build is pinned with a checksum. Other platforms
  need Stockfish on PATH or `PCO_STOCKFISH`.
