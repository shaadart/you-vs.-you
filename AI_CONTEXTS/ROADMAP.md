# ROADMAP

Statuses: PENDING / IN_PROGRESS / COMPLETED / BLOCKED. An iteration is COMPLETED
only when its acceptance criteria have been verified (see PROGRESS.md for evidence).
Iteration scope and acceptance come from `docs/MASTER_SPEC.md` §5. The adaptations
below are justified by Iteration 0 findings.

**All roadmap iterations (0–7) are COMPLETED.** Further work is "Future Research" or owner decisions (see HANDOFF).

| # | Iteration | Status | Depends on |
|---|---|---|---|
| 0 | Repository assessment, feasibility, foundation | COMPLETED | — |
| 1 | Game import and data foundation | COMPLETED | 0 |
| 2 | Playable chess core and human prior | COMPLETED | 0 |
| 3 | Decision episodes, features, analysis | COMPLETED | 1, 2 |
| 4 | Evaluation framework and baselines | COMPLETED | 3 |
| 5 | Personalized move selection V1 | COMPLETED | 4 |
| 6 | Context, temporal behavior, incremental learning | COMPLETED | 5 |
| 7 | Product integration, optimization, release readiness | COMPLETED | 6 |

---

## 0 — Foundation and feasibility — COMPLETED

Delivered: uv/Python 3.12 project, pinned and checksummed assets (`pco setup`),
the `HumanPrior` interface and Maia-3 adapter, a Stockfish candidate evaluator,
`pco doctor`, tests, feasibility benchmarks (EXPERIMENTS.md E0.x), and this
context system.

## 1 — Game import and data foundation — COMPLETED

Delivered: SQLite schema v1 with migrations; PGN, Lichess and Chess.com adapters;
one normalizer; idempotent and cross-source deduplicated import; platform-scoped
accounts linked to local players; `pco import ...`, `pco games` and
`pco games --verify`.

Acceptance, as verified (see PROGRESS.md for the evidence):
- Realistic fixtures import. These are synthetic PGN and JSON in real API shapes,
  plus live imports of 4,666 real games with 0 rejections.
- Invalid input is reported without crashing: illegal moves, Chess960 and other
  variants, missing names, garbage text, non-object JSON, and missing files.
- Games reconstruct exactly: `--verify` replays every stored game, with 0 failures
  across 4,666 live games and all fixtures.
- Repeated imports don't duplicate: re-import gives 0 new; the same game arriving
  from a PGN file and the Lichess API is stored once. Interrupted imports keep
  their progress.

Deferred, on purpose: a re-normalize command from raw data (needed only once the
normalizer changes after release), Chess960 support, and Lichess study or
broadcast import.

## 2 — Playable core and human prior — COMPLETED

Delivered: `GameSession`, the `Opponent` protocol and `PriorOpponent` (Maia-3 5M,
temperature sampling, seedable), and the localhost play page plus JSON API
(`pco play`). Played games are saved as generated PGNs that the importer refuses.

Acceptance, as verified (PROGRESS.md): complete legal games are played through
the session, the HTTP API and a real-Maia test. The page renders live state
(headless Firefox screenshot), and its click and promotion logic passes a node
check. Model outputs are valid: probabilities over legal moves, contract-checked
per move. CPU inference works in live play, with latency and memory recorded in
EXPERIMENTS E2.1 (measured under heavy external load; re-measure on an idle
machine).

Deferred, on purpose: clocks and time controls in play, takeback, deriving the
opponent Elo from the player's games (It. 5), and multiple concurrent games.

## 3 — Decision episodes, features, analysis — COMPLETED

Delivered:
- `DecisionEpisode`, built from pre-move information only.
- The adaptive live candidate set: prior top moves ∪ engine best.
- Training-only, separate evaluation of an out-of-set actual move.
- A cached, resumable, pipelined `DecisionBuilder` with a pool of engine workers.
- Context and candidate features with missing-data flags (FEATURE_VERSION 1).
- `pco build PLAYER [--export]`.
- A faster minimal UCI driver.

Acceptance, as verified (PROGRESS.md):
- Historical decisions replay consistently: an exact pre-move position per
  episode (test), on 7,281 real decisions.
- Analysis is reproducible: a fresh rebuild with different parallelism is
  byte-identical.
- Caches hit 100% on rebuild and invalidate on a model id, node budget or schema
  change (tests).
- Missing clocks and ratings stay unknown and flagged, and ratings are only
  carried forward in time.
- Features respect inference-time availability: a leakage test (truncated game,
  tampered result and post-move clock give identical episodes) and a label-free
  live view.

Deferred, on purpose: opening and familiarity features (It. 4 measures opening
bias first), richer tactical features (add only if It. 5 ablations justify them),
and repetition-aware engine evaluation.

## 4 — Evaluation framework and baselines — COMPLETED

Delivered: `pco eval PLAYER`, which runs:
- chronological game-level splits, with a fingerprint and duplicate-game guard
- predictors over full legal distributions: uniform, generic Maia, Maia+Elo
  offset, Maia+temperature, and an opening book
- metrics: NLL, tie-aware top-k, ECE and reliability
- breakdowns: phase, time class, ply, legal-move count, in/out-of-book, early/late
  test period
- a game-clustered bootstrap vs Maia
- the sampled-quality sweep, with temperature chosen on val

Output is a JSON report.

Acceptance, as verified: reproducible. A cached rerun matched every metric and
parameter, with fixed splits (fingerprint), seeds and model ids. A test also
checks determinism, and that `fit` never sees test games. Baselines are recorded
for two real players with 1,000 games each (EXPERIMENTS E4.1 and E4.2). No
personalization claim is made.

**Bar for It. 5:** beat `book(maia+elo+T)` overall, and improve **out-of-book**
NLL/top1 with a CI excluding 0 (memorization alone cannot do that).

## 5 — Personalized move selection V1 — COMPLETED

Delivered:
- A residual conditional-logit personalization over Maia (+ Elo offset), with the
  opening book on top.
- `Residual` as an evaluation predictor, scored by `pco eval` on the It. 4 splits.
- Paired bootstrap comparisons vs the strongest baselines, and out-of-book CIs.
- `pco train PLAYER`, which writes a versioned JSON profile with a per-player play
  temperature.
- `PersonalOpponent` and `pco play --player NAME`, with a player-likeness score
  labeled as a model estimate.
- Per-position engine-failure handling.

Acceptance, as verified:
- The end-to-end workflow ran on real data: import → build → train → play →
  evaluate (PROGRESS).
- Probabilities are normalized, and every legal move is covered (tests).
- Sampling is stochastic, at the fitted temperature.
- Moves are legal (contract check plus a full-game test).
- Training is reproducible: deterministic Newton, cached analysis.
- Profiles persist.
- Live inference equals offline to 1e-9 (test).
- The held-out result is reported honestly (EXPERIMENTS E5.1): the personal model
  beats every baseline for both players. Out-of-book it beats the tuned prior for
  both, and plain Maia for one, with the other borderline. The effects are small.

## 6 — Context, temporal, incremental — COMPLETED

Delivered:
- Per-decision context (time class, clock bands, rating difference, phase) with
  opt-in interaction groups (`FeatureSpec`).
- A recency-weighted objective, with the half-life chosen per player on val.
- Behavior-change detection: older vs newer halves, a game-clustered CI, reported
  by `pco train`.
- Incremental training: only new games are analyzed, the Elo search warm-starts
  from the previous profile, and the cached vs computed work is printed.
- Training-speed fixes, verified result-neutral.
- An ablation harness, `scripts/ablate_personal.py`.

Acceptance, as verified:
- New games update the profile without rebuilding expensive analysis: +50 games
  retrain in 79 s vs 292 s cold, and an unchanged retrain takes 15 s (E6.4).
- Temporal and contextual benefits and limitations are measured: on three players
  with a pre-registered confirmation that **failed** for the context features,
  and recency helped one player (E6.1–E6.3).
- Shared player identity with contextual adaptation: one profile, with context
  groups available but unused by default because they didn't measurably help.
- Historical games and profiles are preserved: profiles are timestamped files.

## 7 — Product integration and release — COMPLETED

Delivered:
- `pco quickstart` (Chess.com/Lichess → assets → import → train → play) with
  numbered steps and a time estimate.
- Speed presets lite/standard/deep, measured on 3 players, with Lite the default
  (E7.2).
- A training default of the 2,000 most recent games (E7.1), with `--all`.
- Recovery: a corrupt cache is rebuilt, a corrupt DB is left untouched with
  guidance, and an interrupted train resumes.
- `pco doctor` data summary.
- Live play at 1 torch thread.
- A release-oriented README: install, quickstart, presets, measured accuracy,
  limitations, privacy, troubleshooting, licensing.

Acceptance, as verified:
- The core journey works on the supported CPU hardware: a fresh-home quickstart
  in 352 s, and the owner's own 9.5k-game import and training (E7.3, PROGRESS).
- Performance is measured per preset (E7.2, E7.3).
- Evaluation is reproducible (fixed splits, fingerprints, deterministic reports).
- Limitations are described honestly (README "What it is not", ARCHITECTURE
  "Known limitations").

Not done, recorded: macOS and Windows runs are unverified; there are no
per-platform Elo offsets; the license decision is the owner's.

## Interface iterations (owner requests after 7) — COMPLETED

- **Mirror** (2026-10-26): the visual identity (docs/DESIGN.md, D37).
- **Twin** (2026-10-26): the experience of playing yourself. It covers home,
  versions (years and time controls), whispers, the reflection with "see the
  difference", the archive, the portrait, settings with import/rebuild/forget,
  and staged builds (D38–D43, PROGRESS).
- **Symphony** (2026-10-26): refinements. Geist Pixel titles, a home that
  demonstrates itself, real time-left estimates, and continuity of background work
  (D44–D48, E8.1).

## Future research (spec §5; only with measurement)

- Per-platform rating offsets for mixed-platform players.
- Richer residual models (compact embeddings, nonlinear rerankers), judged
  out-of-book with paired CIs.
- Clocked play, so learned time-pressure behavior can show live.
- Behavioral era detection beyond the two-halves test; "Current me vs historical
  me" profiles from `--recent` windows.
- Blind human evaluation of real vs generated games.
