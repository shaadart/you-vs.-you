# ROADMAP

Statuses: PENDING / IN_PROGRESS / COMPLETED / BLOCKED. An iteration is COMPLETED
only when its acceptance criteria have been verified (see PROGRESS.md for evidence).
Iteration scope and acceptance come from `docs/MASTER_SPEC.md` §5. The adaptations
below are justified by Iteration 0 findings.

**Current iteration: 5 (PENDING, next to start).**

| # | Iteration | Status | Depends on |
|---|---|---|---|
| 0 | Repository assessment, feasibility, foundation | COMPLETED | — |
| 1 | Game import and data foundation | COMPLETED | 0 |
| 2 | Playable chess core and human prior | COMPLETED | 0 |
| 3 | Decision episodes, features, analysis | COMPLETED | 1, 2 |
| 4 | Evaluation framework and baselines | COMPLETED | 3 |
| 5 | Personalized move selection V1 | PENDING | 4 |
| 6 | Context, temporal behavior, incremental learning | PENDING | 5 |
| 7 | Product integration, optimization, release readiness | PENDING | 6 |

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

## 5 — Personalized move selection V1 — PENDING

Planned first model (to be validated against the Iteration 4 baselines):
residual conditional logit
`log P(m) = log P_maia(m) + f_θ(φ(m, s)) − log Z`,
where f is nonzero only on the candidate set and non-candidates keep their prior
log-probability, so the distribution always covers every legal move. θ uses L2
regularization toward 0 (that is, toward Maia), and the regularization strength is
chosen on chronological validation. The profile also stores a fitted **play
temperature**, chosen for sampled-quality match on validation games (E2.3); this
is separate from the NLL-optimal temperature used for likelihood scoring.

Inputs from It. 4:
- **Keep the book as the repertoire component.** The likely structure is
  `book(residual(maia+elo))`. Fit the residual f_θ so it adds *out-of-book* gains,
  which is the claim that matters (E4 finding 3). It must implement the
  `Predictor` protocol to be scored by `pco eval`.
- Training data needs engine analysis of the train split as well: about 70% of
  decisions, about 7 min per 1,000 games cold at E3.1 rates. `pco eval` currently
  analyzes only val+test.
- The play temperature differed per player (0.7 vs 1.0, E4.1/E4.2). Fit it per
  profile on val using `quality_matching_temperature`.
- Personalization success must be reported on the same splits (fingerprints
  `c3a0cae2ad7e9b7b`, `a9726602c78de4a9`) against the recorded baseline numbers.

## 6 — Context, temporal, incremental — PENDING
## 7 — Product integration and release — PENDING

Scope as in the spec. Hardware profiles (Lite/Standard/Deep) map to K (candidate
count), the Stockfish node budget, and possibly the Maia-3 size (5M vs 23M; E0.1
has the cost data).
