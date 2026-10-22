# ROADMAP

Statuses: PENDING / IN_PROGRESS / COMPLETED / BLOCKED. An iteration is COMPLETED
only when its acceptance criteria have been verified (see PROGRESS.md for evidence).
Iteration scope and acceptance come from `docs/MASTER_SPEC.md` §5. The adaptations
below are justified by Iteration 0 findings.

**Current iteration: 2 (PENDING, next to start).**

| # | Iteration | Status | Depends on |
|---|---|---|---|
| 0 | Repository assessment, feasibility, foundation | COMPLETED | — |
| 1 | Game import and data foundation | COMPLETED | 0 |
| 2 | Playable chess core and human prior | PENDING | 0 (prior adapter exists) |
| 3 | Decision episodes, features, analysis | PENDING | 1, 2 |
| 4 | Evaluation framework and baselines | PENDING | 3 |
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

## 2 — Playable core and human prior — PENDING

Scope: a game session (board state, legality, termination including
threefold/50-move/insufficient material), an opponent interface (`choose_move`)
implemented first by `PriorOpponent(Maia3Prior)` with temperature sampling, and a
minimal local UI.
UI decision (to confirm at start): a localhost single page served by the stdlib HTTP
server with an inline-SVG board and no CDN (works offline), plus a terminal
fallback for tests. Record latency and memory for real games.
Acceptance (spec): a complete legal game is playable, outputs are valid, and
latency and memory are recorded.

## 3 — Decision episodes, features, analysis — PENDING

Scope: `DecisionEpisode` built only from information available before the move.
Candidate set = top-K of the prior ∪ the engine's best move ∪ (training only) the
actual move. Stockfish candidate scores come from node-limited searches cached by
(position, candidate set, engine id, nodes). Includes clock context, phase and
basic tactical features. The prior and engine are computed only for the player's
own moves.
Budget (E0.2/E0.3): the prior runs at about 190 positions/s (batch 64, 4 threads),
and Stockfish at 10k nodes costs about 18 ms per position. That is about 15 to 30
minutes for 100k decisions on one core each, so build incrementally, cache, and
parallelize engine work across processes. Default engine budget to validate:
K=5, 10k nodes.

## 4 — Evaluation framework and baselines — PENDING

Chronological, game-level splits with duplicate-game protection. Metrics:
top-1/top-k, actual-move NLL, calibration (ECE or reliability), and latency.
Breakdowns by phase, time control, in-book vs out-of-book, and number of plausible
moves.
Baselines: uniform legal; generic Maia-3 at the player's rating; Maia-3 at a
**fitted Elo** (a one-parameter personalization baseline, cheap and strong); and a
per-player opening-frequency table blended with the prior.

## 5 — Personalized move selection V1 — PENDING

Planned first model (to be validated against the Iteration 4 baselines):
residual conditional logit
`log P(m) = log P_maia(m) + f_θ(φ(m, s)) − log Z`,
where f is nonzero only on the candidate set and non-candidates keep their prior
log-probability, so the distribution always covers every legal move. θ uses L2
regularization toward 0 (that is, toward Maia), and the regularization strength is
chosen on chronological validation.

## 6 — Context, temporal, incremental — PENDING
## 7 — Product integration and release — PENDING

Scope as in the spec. Hardware profiles (Lite/Standard/Deep) map to K (candidate
count), the Stockfish node budget, and possibly the Maia-3 size (5M vs 23M; E0.1
has the cost data).
