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
  analysis/stockfish.py [impl] StockfishEvaluator.evaluate(board, candidates, AnalysisLimit(nodes)) -> CandidateEval[]
  cli.py              [impl] `pco setup | doctor | import {pgn,lichess,chesscom} | games [--verify]`
  storage/db.py       [impl] connect() (WAL, foreign keys) + ordered migrations via schema_version
  storage/migrations.py [impl] schema v1 (raw + normalized layers)
  storage/games.py    [impl] repository: runs, raw records, games, accounts, summary, verify
  importers/sources.py  [impl] adapters -> RawRecord(kind, text): pgn file split, Lichess NDJSON, chess.com archives
  importers/normalize.py [impl] RawRecord -> NormalizedGame (one PGN path for all sources)
  importers/pipeline.py  [impl] import_records(): raw store -> normalize -> dedup insert -> account linking
  importers/http.py   [impl] stdlib HTTP client: User-Agent, 429/5xx retry, injectable
  play/               [planned, It.2] game session, Opponent protocol, local UI
  decisions/          [planned, It.3] DecisionEpisode builder, features, analysis cache
  evaluation/         [planned, It.4] splits, metrics, baselines, `pco eval`
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
- **Opponent.choose_move(game_state) → move** [planned]. Implementations:
  PriorOpponent (It. 2) and PersonalOpponent (It. 5). The UI never knows which
  model it is talking to, beyond the label it displays.

## Data flow (first line implemented in It. 1; the rest planned)

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
3. **Decision data [It. 3]**
   - `prior_cache(position_key, history_key, model_id, elo_self, elo_oppo, logprobs BLOB)`
   - `engine_cache(position_key, candidates_key, engine_id, nodes, evals BLOB)`
   - Episode features are recomputed from the caches, or materialized with a
     `feature_version`.
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

## Known limitations

- Maia-3's encoder uses a perspective-mirrored board history. For a FEN without a
  move stack, history is padded with the current position, which is measurably
  weaker (E0.1).
- Prior throughput is compute-bound on the model forward pass, not tokenization
  (profiled: about 90% forward). Best offline throughput is batch 64: about 190
  positions/s at 4 threads and about 250 at 10 threads. The
  `predict(...)` batch size is the caller's choice; offline builders should use 64.
- Process RSS is about 520 MB with the 5M prior loaded, mostly the torch runtime.
  An ONNX Runtime export is a possible Lite-profile optimization (It. 7, unmeasured).
- Normalizer changes after release must bump `NORMALIZER_VERSION`. A
  re-normalize command from `raw_games` doesn't exist yet; the raw data keeps it
  possible.
- Incremental Lichess imports re-fetch from 24 h before the last complete run.
  Correspondence games older than that, finishing later, need `--full`.
- Chess960 and other variants are rejected (stored raw, with a reason). Standard
  games starting from a custom FEN are accepted.
- Only the Linux x86-64 Stockfish build is pinned with a checksum. Other platforms
  need Stockfish on PATH or `PCO_STOCKFISH`.
