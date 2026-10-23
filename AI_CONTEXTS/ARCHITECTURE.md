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
  cli.py              [impl] `pco setup | doctor | import {pgn,lichess,chesscom} | games [--verify] | play | build | eval`
  storage/db.py       [impl] connect() (WAL, foreign keys) + ordered migrations via schema_version
  storage/migrations.py [impl] schema v1 (raw + normalized layers)
  storage/games.py    [impl] repository: runs, raw records, games, accounts, summary, verify
  importers/sources.py  [impl] adapters -> RawRecord(kind, text): pgn file split, Lichess NDJSON, chess.com archives
  importers/normalize.py [impl] RawRecord -> NormalizedGame (one PGN path for all sources)
  importers/pipeline.py  [impl] import_records(): raw store -> normalize -> dedup insert -> account linking
  importers/http.py   [impl] stdlib HTTP client: User-Agent, 429/5xx retry, injectable
  play/opponent.py    [impl] Opponent protocol, MoveChoice, PriorOpponent (samples the prior; no fallback)
  play/session.py     [impl] GameSession: turns, legality, termination, resignation, generated-PGN export
  play/server.py      [impl] PlayApp + stdlib ThreadingHTTPServer on 127.0.0.1 (JSON API)
  play/static/index.html [impl] self-contained page: inline-SVG board, click-to-move, promotion picker
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
  personal/           [planned, It.5] residual personalization model, training, artifacts
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
- **Play HTTP API** [impl]: `GET /`, `GET /api/state`, `GET /api/pgn`, and
  POST `/api/new {color, elo, temperature}`, `/api/move {uci}`, `/api/opponent`,
  `/api/resign`. The human move and the opponent reply are separate requests, so
  the page shows the human move at once. Errors return 409 with the current state.

## Data flow (first two lines implemented in It. 1–3; the rest planned)

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
   - `profiles(id, player_id, model_kind, model_version, train_config JSON,
     data_fingerprint, artifact_path, metrics JSON, created_at)`.
     Artifacts are files under `$PCO_HOME/profiles/`.

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

## Known limitations

- Play: one game at a time per server, no takeback, no clocks or time control.
  The opponent Elo is chosen by the user, not derived from their games. Pieces are
  Unicode glyphs, which depend on system fonts (DejaVu, Segoe UI Symbol and Noto
  are listed as fallbacks).

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
- Only the Linux x86-64 Stockfish build is pinned with a checksum. Other platforms
  need Stockfish on PATH or `PCO_STOCKFISH`.
