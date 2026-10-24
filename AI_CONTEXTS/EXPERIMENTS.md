# EXPERIMENTS

Measured results only. Each entry gives its setup so it can be reproduced.
Hardware for all Iteration 0 entries: Intel i5-1235U (10 cores, 12 threads),
15 GB RAM, Linux (CachyOS), no GPU. torch 2.8.0+cpu, python-chess 1.11.2.

## Dataset D0: public Lichess arena games (feasibility only)

- Source: Lichess tournament export API. Arena `1nLI7ZTS` (≤2000 Rapid, 10+0) has 312
  games and arena `CpEqUfrh` (≤1300 Blitz, 5+0) has 123. Both were played on
  2026-10-22 and fetched 2026-10-23.
- Reproduce with `scripts/fetch_feasibility_data.sh DIR`. The games are not
  committed: they are third-party data, and the arena export could change if
  games are removed.
- 435 games and 26,825 positions (every move, both colors), with ratings from both
  PGN headers. This is a **generic** population sample, not a single player, so it
  says nothing about personalization.

## E0.1: Human-prior comparison (2026-10-23)

Script: `scripts/feasibility_priors.py` (one model per process; `--group bench` for
Maia-2). The prior is conditioned on each mover's own rating and the opponent's
rating, and the metrics cover the actual move. NLL is the mean −log P(actual move)
in nats. The subset "ply≥20, non-forced" has n=18,463. Latency is for one position
per call (first 200 positions) at 4 torch threads. maxRSS is the process peak.

| Model | Params | Weights | top-1 all | top-3 all | NLL all | top-1 ply≥20 | NLL ply≥20 | latency p50 / p95 | maxRSS |
|---|---|---|---|---|---|---|---|---|---|
| uniform over legal moves | — | — | 0.070 (expected) | — | 3.187 | — | — | — | — |
| **Maia-3 5M (8-ply history)** | 5.23M | 20 MiB | **0.561** | 0.835 | **1.325** | 0.571 | 1.297 | 13.5 / 23.6 ms | 525 MB |
| Maia-3 5M, no history (current position repeated) | 5.23M | 20 MiB | 0.542 | 0.823 | 1.392 | 0.550 | 1.365 | 20.4 / 25.5 ms | 524 MB |
| Maia-3 23M (8-ply history) | 22.9M | 88 MiB | 0.568 | 0.840 | 1.294 | 0.580 | 1.257 | 41.0 / 45.6 ms | 721 MB |
| Maia-2 rapid | 23.3M | 267 MiB | 0.513 | 0.790 | 1.493 | 0.542 | 1.396 | 15.3 / 25.4 ms | 603 MB |
| Maia-2 blitz | 23.3M | 267 MiB | 0.511 | 0.785 | 1.518 | 0.540 | 1.408 | 13.0 / 13.8 ms | 616 MB |

Findings:
- Maia-3 5M dominates Maia-2 on accuracy, NLL and size. It was chosen (ARCHITECTURE D1).
- History matters: +1.9 points top-1 and −0.067 NLL. Always pass the real move stack.
- 23M gains +0.7 points top-1 and −0.030 NLL at about 3x latency and 0.2 GB more RSS.
  It is a candidate "Deep" profile.
- Maia-2's gap is largest in the opening (all positions vs ply≥20). That is
  consistent with Maia-3's newer training data (2023–2025), but the cause was not
  investigated.
- Caveat: 435 games, one day, two arenas, and the test games are newer than any
  model's training data. Treat differences under about 0.5 points as noise. No
  confidence intervals were computed.
- Maia-2 rapid/blitz weights download from Google Drive via gdown. The blitz
  "load 34 s" figure includes its download.

## E0.2: Maia-3 5M CPU throughput profile (2026-10-23)

Real positions (1,024) through `Maia3Prior`: encode (tokenize history) took 0.71 s,
the legal-move masks 0.11 s, and the model forward 7.28 s at batch 256 with 4
threads. The bottleneck is compute in the forward pass, not Python preprocessing.
Forward-only throughput on random input, in positions/s:

| threads | batch 1 | batch 64 | batch 256 |
|---|---|---|---|
| 1 | 78 | 112 | 97 |
| 2 | 74 | 157 | 137 |
| 4 | 103 (9.7 ms) | 190 | 151 |
| 8 | 71 | 239 | 186 |
| 10 | 57 | 250 | 214 |

Implications: use 4 threads for live play (about 10 ms per move) and batch 64 for
offline building. Single-thread live inference (about 13 ms) is fine for a Lite
profile.

## E0.3: Stockfish 19 candidate scoring cost and fidelity (2026-10-23)

Script: `scripts/feasibility_stockfish.py`. 200 random positions from D0 with ply ≥10
and ≥8 legal moves. Candidates are the Maia-3 top-K. Each call is `root_moves` +
MultiPV=K on a single thread with 64 MB hash and `ucinewgame` per call. The
reference is 200k nodes. "cp err" is the mean |cp − ref cp| with both clipped to
±1000. "agree" is how often the best candidate matches the reference's best.

| K | nodes | p50 / p95 | mean cp err | best agree |
|---|---|---|---|---|
| 3 | 2k | 10.3 / 13.8 ms | 65.7 | 0.73 |
| 3 | 10k | 17.7 / 20.4 ms | 50.0 | 0.81 |
| 3 | 50k | 59.5 / 67.5 ms | 28.3 | 0.87 |
| 5 | 2k | 10.4 / 12.9 ms | 64.9 | 0.63 |
| 5 | 10k | 18.8 / 22.2 ms | 46.6 | 0.77 |
| 5 | 50k | 58.1 / 67.9 ms | 29.2 | 0.80 |
| 8 | 2k | 10.4 / 12.7 ms | 78.9 | 0.59 |
| 8 | 10k | 17.4 / 20.3 ms | 55.3 | 0.62 |
| 8 | 50k | 57.1 / 65.4 ms | 32.8 | 0.79 |

Findings: cost depends on the node budget, not K, because MultiPV shares the node
budget. There is a floor of about 10 ms per call. Per-candidate fidelity falls as K
grows at fixed nodes. Whether a 50 cp error matters for *behavior prediction* is
unknown; decide with Iteration 4/5 ablations, not by engine-accuracy intuition.

## E0.4: Platform API availability probe (2026-10-23)

- Chess.com `GET api.chess.com/pub/player/{user}/games/archives` returned 200
  without auth. A monthly archive gives JSON games with `pgn` (including `%clk`),
  `uuid`, `time_class`, `time_control`, `rules`, `rated` and `end_time`.
- Lichess `GET /api/games/user/{username}` returned **404 for every username tried**
  (existing accounts, NDJSON and PGN), without a token. Meanwhile
  `/game/export/{id}`, `/api/tournament/{id}/games` and `/api/user/{name}` returned
  200. The published OpenAPI spec still documents anonymous access at 20 games/s.
  The cause is unknown (possibly a new auth requirement, or blocking at the edge for
  this network). It is unverified with a token. See ROADMAP It. 1 for mitigations.
- **Update 2026-10-23 (Iteration 1): cause found.** Lichess returns 404 for the
  user-games export when the User-Agent is curl's default (`curl/8.0`). With
  `Python-urllib/3.12`, `Mozilla/5.0` or a descriptive UA (`pco/0.0.1 ...`) the
  same request returns 200, anonymously. The E0.4 probe used curl defaults. pco
  always sends `pco/<version> (local-first personal chess opponent)`. No token is
  needed.

## E1.1: Import pipeline on live data (2026-10-23)

Scratch `PCO_HOME`; real public accounts, used only as format and robustness test
data (not committed).

| Import | Records | New | Dup | Rejected | Wall time | Notes |
|---|---|---|---|---|---|---|
| Lichess `thibault --max 1000` | 1,000 | 1,000 | 0 | 0 | 41 s | ≈24 games/s; Lichess throttles anonymous streams to 20–30/s |
| Lichess `thibault --max 2000` | 2,000 | 2,000 | 0 | 0 | — | all 6 time classes present (ultrabullet..classical + correspondence) |
| Chess.com `Hikaru`, archives 2026-06..10 | 2,666 | 2,666 | 0 | 0 | 11.9 s | 6 monthly requests; peak RSS 49 MB |
| Local PGN, arena `1nLI7ZTS` | 312 | 312 | 0 | 0 | 0.75 s | includes interpreter start |
| Re-import of the same PGN | 312 | 0 | 312 | 0 | — | raw rows unchanged (312) |
| Arena PGN, then Lichess `LonelySheperd --max 300` | 300 | 294 | 6 | 0 | — | the 6 arena games are deduplicated across sources |

- `pco games --verify` replayed all 4,666 games in the combined DB (and the 606 in
  the cross-source DB) with **0 failures**.
- The DB with 4,666 games plus raw records is 30 MB, about 6.4 KB per game.
  Raw records are about half of that.
- Format findings that changed the code:
  1. Lichess correspondence games use `TimeControl "14 days per move"`. They are
     now classified as correspondence (3 of 2,000 games were unclassified before).
  2. Lichess ignores `max` when `perfType` is also given (got 50 for `max=40`), so
     `max` is now also enforced client-side.
  3. The Lichess Site header uses the form `https://lichess.org/game/<id>`, and a
     `GameId` header is present. Both forms are parsed.

## E2.1: Live play latency and memory (2026-10-23)

Script: `scripts/bench_play.py` (both sides are `PriorOpponent(Maia-3 5M, Elo 1500)`
through `GameSession`, temperature 1). **Measured under heavy external load:** a
non-pco Java process was using about 590% CPU, the system load average was
**14–16 on 12 hardware threads**, and RAM was 14/15 GB used with swap active. Treat
these as pessimistic, and re-measure on an idle machine. The idle single-position
reference is E0.2 (about 10 ms at 4 threads).

| torch threads | games | plies | latency p50 | p95 | p99 | max | peak RSS |
|---|---|---|---|---|---|---|---|
| 4 | 10 | 1,068 | 42.0 ms | 103.0 ms | 218.9 ms | 394.2 ms | 260 MB |
| 1 | 5 | 406 | 37.1 ms | 82.3 ms | 99.2 ms | 156.0 ms | 265 MB |

- Prior load time is 0.14–0.18 s. All 15 games ended normally (14 checkmates, 1
  stalemate), and none hit the ply cap.
- The `pco play` server process peaked at 266 MB (`VmHWM`) after a few moves.
- Under this load, 1 thread was no slower than 4 (oversubscription). Default
  threads for live play should be revisited on an idle machine (It. 7 hardware
  profiles).
- Live play through the HTTP API with real Maia (seed 1) reproduced the identical
  replies e6, d5, dxc4 on two separate server runs.

## E2.2: Calling-thread effect on inference latency (2026-10-23)

The same loaded machine as E2.1, with a single position after 1.e4 and 10 calls
each. Same thread: median 45.9 ms. A fresh thread per call (what
`ThreadingHTTPServer` does): 56.3 ms. One persistent worker thread: 43.4 ms. That
is about 10 ms saved per move, so opponent inference runs on one persistent
worker (ARCHITECTURE D15).

## E2.1b: Live play latency re-measured on an idle machine (2026-10-23)

Same script and settings as E2.1, at load average about 2 (no external load).
These numbers supersede E2.1 as the reference.

| torch threads | games | plies | p50 | p95 | p99 | max | peak RSS |
|---|---|---|---|---|---|---|---|
| 4 | 10 | 1,048 | 20.2 ms | 24.7 ms | 28.2 ms | 46.7 ms | 264 MB |
| 1 | 5 | 403 | **13.5 ms** | 16.1 ms | 17.2 ms | 18.5 ms | 268 MB |

For single-position live inference, 1 thread is faster than 4 because thread
overhead dominates at batch 1. Revisit the `torch_threads` default for live play in
It. 7. Keep more threads for offline batch building (E0.2). These games used the new
default T=0.7, which doesn't affect latency.

## E2.3: Sampled-prior strength vs real humans (2026-10-23)

**Trigger:** the owner reported that the play opponent (Maia-3 5M, Elo 1500, T=1)
made more mistakes than expected and felt weaker than a Maia they had played
before.

**Check 1: adapter correctness.** `Maia3Prior` scored top-1 0.5717 and NLL 1.2859
on 8,000 D0 positions, consistent with E0.1 (0.5645/1.3045 on 9,429 positions) and
the published 57.1%. No encoding bug was found.

**Check 2: strength.** Script `scripts/strength_check.py`. Random D0 positions with
ply ≥ 8 and a mover rated 1300–1700, with the prior conditioned on the mover's
rating. Each policy picks one move per position. Every pick, plus the engine's best
move, is scored in one Stockfish 19 `root_moves` search at 50k nodes. Loss is
measured against the best candidate, with cp clipped to [0, 1000]; wp is a
logistic win probability (400 cp scale). n = 1,500 per run, one sample per position.

| policy | run A cp loss | A blunder ≥300 | run B cp loss | B mistake ≥100 | B blunder ≥300 |
|---|---|---|---|---|---|
| actual human move | 63.2 | 5.1% | 72.1 | 16.9% | 6.9% |
| argmax (like the Lichess Maia bots) | 45.6 | 3.6% | 55.3 | 13.5% | 4.7% |
| sample T=1 (old play default) | 83.4 | 7.6% | 95.5 | 21.5% | 9.5% |
| sample T=0.8 | — | — | 77.1 | 18.7% | 7.8% |
| sample T=0.7 | — | — | 77.2 | 18.1% | 7.2% |
| sample T=0.6 | — | — | 73.1 | 17.4% | 6.6% |
| sample T=0.5 | 56.3 | 4.5% | 62.6 | 14.8% | 5.4% |

Run A used seed 0 and run B seed 1. The seed also selects the positions, so
absolute levels differ between runs; compare within a run. The mean cp-loss
standard error is roughly ±4 cp per cell (estimated, not computed), so T=0.6 and
T=0.7 are not distinguishable.

NLL of the actual human move under temperature-scaled policy (run B positions):
T0.5 1.529, T0.6 1.398, T0.7 1.325, T0.8 1.288, **T0.9 1.275**, T1.0 1.277,
T1.1 1.290, T1.2 1.311.

**Findings**
1. The owner's observation is real. **Sampling at T=1 is clearly weaker than the
   humans it models:** 32–37% more cp loss and 38–49% more blunders. Argmax is
   stronger than those humans, which is consistent with "the Maia I played before"
   being argmax-based (the Lichess Maia bots). That is likely but not verified.
2. **Likelihood calibration and behavioral quality disagree.** NLL is near-optimal
   at T≈0.9–1.0, so each decision's probabilities are roughly calibrated, but whole
   samples are too erratic. Interpretation (hypothesis): a population model spreads
   mass over moves that *different* players at this rating would play, so it
   carries population-level and epistemic uncertainty. One sample from that mixture
   per move is noisier than any single consistent human. Personalization should
   narrow this, and that is testable in It. 5.
3. Action: the play default is now **T=0.7** (provisional, generic opponent only).
   It is in the range where cp loss and blunder rate match the real humans.
   Temperature becomes a measured, per-player parameter. It. 4 adds a
   "sampled-quality match" metric next to NLL, and It. 5 fits it on held-out data.

## E3.1: Decision-dataset build at realistic scale (2026-10-23)

Lichess `thibault`: the 200 most recent games (imported with `--max 200`), 7,281
own decisions. Default `AnalysisConfig` (k_max 5, mass 0.95, 10k nodes for the
set, 5k for the best move), 4 engine workers, torch 4 threads, idle machine
(load about 1–4), new UCI driver, pipelined builder.

- Cold build: **126.2 s, 58 decisions/s**. A cached rebuild takes 4.9 s (1,495/s,
  0 engine and prior misses). Within the cold build, 1,233 engine hits came from
  repeated positions (openings).
- Cache file: 7.3 MB (about 1.0 KB per decision). JSONL export: 18 MB.
- Reproducibility: on 20 games, a fresh-cache rebuild with 2 workers was
  byte-identical to the 4-worker build, and a cached rebuild was byte-identical
  to the first build (after the canonical-precision fix; see PROGRESS).
- Dataset profile:
  - 69 forced positions.
  - The live set averages 4.28 moves (sizes 1–6).
  - The actual move is in the live set **93.1%** of the time; the other 6.9% are
    evaluated separately.
  - The actual move is the prior's top-1 58.9% of the time and the engine best
    41.1%.
  - Mean cp loss of the actual move is 55.0, with 4.1% blunders (≥300 cp).
  - Phases: opening 2,257 / middlegame 2,817 / endgame 2,207.
  - Elo 1698–1858, clocks present in 100% of decisions.

## E3.2: Engine-evaluation throughput and thread scaling (2026-10-23)

Idle machine. 240 positions from E3.1, 6 root moves each, MultiPV 6, 10k nodes,
`ucinewgame` per search. Searches/s by thread count (one engine process per
thread):

| driver | hash | 1 | 2 | 4 | 8 |
|---|---|---|---|---|---|
| python-chess `analyse`, INFO_ALL | 64 MB | 52 | 72 | 78 | 87 |
| python-chess `analyse`, SCORE\|PV | 64 MB | 51 | 73 | 87 | 91 |
| python-chess, SCORE\|PV | 4 MB | 62 | 92 | 109 | 113 |
| minimal UCI driver | 64 MB | 58 | — | 108 | 112 |
| minimal UCI driver | 16 MB | 64 | — | 135 | 166 |
| minimal UCI driver | **4 MB** | 65 | — | **147** | **184** |

- Two separate limits. (1) `ucinewgame` clears the whole hash table on every
  search, and with 64 MB that clearing is memory-bandwidth-bound and stops thread
  scaling. (2) python-chess's per-line asyncio processing holds the GIL and caps
  multi-engine throughput (109 vs 147 at 4 threads with the same 4 MB hash).
- Process pools were tried. With both `fork` and `spawn`, the workers finished
  their task, but the pool never shut down because python-chess's engine keeps a
  non-daemon thread. That approach was abandoned for the minimal driver.
- End-to-end builder effect, 663 decisions, 4 workers: 19.9 s before, 12.7 s
  after the driver and hash change, 12.3 s with prior/engine pipelining.
  Pipelining matters more for long runs with many chunks.
- Correctness: the driver equals python-chess move-for-move and cp-for-cp on test
  positions with identical settings, including the last-line-may-be-a-bound
  behavior.

## E4.1–E4.2: Baselines on held-out future games (2026-10-23)

Command: `pco eval PLAYER` with defaults:
- split: the oldest 70% of games for train, the next 10% for val, the newest 20%
  for test
- default `AnalysisConfig`, prior `maia3-5m@ba14208b2992`, engine
  `Stockfish 19|threads=1|hash=4`
- quality sweep at temperatures {0, 0.5, 0.6, 0.7, 0.8, 1.0}, 2 samples per
  position, seed 0, 1,000 game-level bootstrap resamples

Metrics cover non-forced test decisions. All tuning (Elo offset, temperature, book
alpha, quality temperature) happened on val only. Data: each player's 1,000 most
recent public Lichess games (scratch homes, not committed).

### E4.1: `thibault`, Lichess blitz ≈1700–1860

- Split `c3a0cae2ad7e9b7b`: 697/100/199 games. Test starts 2026-09-12. 7,191 test
  decisions scored, 69 forced excluded. 868 (12.1%) are in-book.
- Cold run 816 s. A cached rerun took 150 s and reproduced every metric, parameter
  and quality number exactly.

| predictor | NLL | top1 | top3 | ECE | ΔNLL vs maia [95% CI] | in-book top1 / NLL | out-of-book top1 / NLL |
|---|---|---|---|---|---|---|---|
| uniform | 3.2393 | 0.056 | 0.161 | 0.000 | +2.0071 [+1.9596, +2.0537] | 0.041 / 3.285 | 0.058 / 3.233 |
| maia (game Elo) | 1.2322 | 0.589 | 0.859 | 0.028 | reference | 0.619 / 1.135 | 0.584 / 1.245 |
| maia+T (T=0.95) | 1.2295 | 0.589 | 0.859 | 0.016 | −0.0027 [−0.0039, −0.0014] | 0.619 / 1.124 | 0.584 / 1.244 |
| maia+elo (+100) | 1.2252 | 0.588 | 0.859 | 0.021 | −0.0070 [−0.0088, −0.0052] | 0.615 / 1.126 | 0.584 / 1.239 |
| maia+elo+T | 1.2238 | 0.588 | 0.859 | 0.019 | −0.0084 [−0.0109, −0.0060] | 0.615 / 1.115 | 0.584 / 1.239 |
| book(maia+elo+T), α=2 | **1.1678** | **0.606** | 0.866 | 0.012 | **−0.0644** [−0.0738, −0.0558] | **0.763 / 0.651** | 0.584 / 1.239 |

Breakdowns, maia top1/NLL:
- By phase: opening 0.595/1.221, middlegame 0.545/1.372, endgame 0.638/1.066.
- By legal-move count: 2–10 moves 0.739/0.645, 11–25 moves 0.624/1.094, 26+ moves
  0.557/1.357.
- By test half: early 0.587/1.231, late 0.590/1.233 (no drift within the test
  period).
- The book's gain is entirely in the opening phase (0.595 → 0.652 top1); middlegame
  and endgame are unchanged.
- Time class is 96% blitz. Rapid has n=260, and classical n=1, which is too small
  to read.

Sampled quality on test (mean cp loss / blunder rate):
- The actual player: **55 / 4.1%**.
- maia sampled at T=0: 41 / 2.7%; T=0.5: 49/3.5%; T=0.6: 51/3.7%; **T=0.7:
  55/4.0%**; T=0.8: 58/4.3%; T=1.0: 66/5.5%.
- The temperature chosen on val (0.7) matches on test.
- maia+elo and the book behave similarly; their val-chosen T was 0.7.

### E4.2: `warpentak`, Lichess blitz ≈1220

- Split `a9726602c78de4a9`: 680/96/194 games. Test starts 2026-09-05. **One
  held-out game was dropped as a duplicate move sequence of an earlier game.**
  6,118 decisions scored, 54 forced excluded. 752 (12.3%) are in-book.
- Cold run 1,152 s.

| predictor | NLL | top1 | top3 | ECE | ΔNLL vs maia [95% CI] | in-book top1 / NLL | out-of-book top1 / NLL |
|---|---|---|---|---|---|---|---|
| uniform | 3.2253 | 0.058 | 0.164 | 0.000 | +1.6709 [+1.6237, +1.7143] | 0.040 / 3.247 | 0.060 / 3.222 |
| maia (game Elo) | 1.5544 | 0.502 | 0.772 | 0.024 | reference | **0.338 / 2.338** | 0.525 / 1.445 |
| maia+T (T=1.05) | 1.5529 | 0.502 | 0.772 | 0.022 | −0.0014 [−0.0027, −0.0001] | 0.338 / 2.307 | 0.525 / 1.447 |
| maia+elo (offset 0) | 1.5544 | 0.502 | 0.772 | 0.024 | +0.0000 | 0.338 / 2.338 | 0.525 / 1.445 |
| maia+elo+T | 1.5529 | 0.502 | 0.772 | 0.022 | −0.0014 [−0.0027, −0.0001] | 0.338 / 2.307 | 0.525 / 1.447 |
| book(maia+elo+T), α=2 | **1.3589** | **0.562** | 0.812 | 0.028 | **−0.1954** [−0.2130, −0.1783] | **0.828 / 0.729** | 0.525 / 1.447 |

Sampled quality on test (mean cp loss / blunder rate):
- The actual player: **77 / 6.3%**.
- maia at T=0: 49/3.5%; T=0.5: 56/4.1%; T=0.7: 63/5.0%; T=0.8: 69/5.5%; **T=1.0:
  82/7.2%**.
- The val-chosen T was **1.0** (maia) and 0.8 (book).

### Findings (both players)

1. **Generic Maia predicts these players' future moves at 50–59% top-1, and no
   generic adjustment helps much.** The fitted Elo offset and NLL temperature
   change NLL by less than 0.01. They are statistically significant for thibault,
   but tiny.
2. **The opening book is the only strong baseline, and its gain is pure
   repertoire.** It gives −0.064 and −0.195 NLL, with in-book top1 76–83%, but
   out-of-book metrics are identical to its base. About 12% of test decisions are
   in-book. For warpentak, Maia is *bad* in their repertoire (in-book top1 0.338),
   which suggests idiosyncratic openings.
3. **Bar for It. 5:** a personalized model must beat `book(maia+elo+T)`. To claim
   learned *style* rather than memorization, it must improve **out-of-book**
   NLL/top1 (thibault 1.239/0.584, warpentak 1.447/0.525) with a bootstrap CI
   excluding 0.
4. **The quality-matching temperature is player-dependent.** It was 0.7 for
   thibault (≈1800) and about 1.0 for warpentak (≈1220). E2.3's "T=1 is too weak"
   held for the 1300–1700 arena population and thibault, but not for this
   lower-rated player, whose own move quality is closer to T=1 samples. So the play
   temperature must be fitted per player (D16, It. 5), and the generic 0.7 default
   remains only a fallback.
5. NLL-optimal T (0.95–1.05) ≠ quality-matching T (0.7–1.0). The distinction (E2.3)
   is confirmed on held-out data for one of two players.
6. Calibration is good for all Maia variants (ECE 0.012–0.028).

## E5.1: Personalization v1 on held-out future games (2026-10-23)

Same command, splits and settings as E4 (fingerprints `c3a0cae2ad7e9b7b`,
`a9726602c78de4a9`, unchanged). New predictors:
- `residual(maia+elo)`: the residual conditional logit over the live set (23
  features, ARCHITECTURE D26). It is trained on the train split (23,299 and
  21,603 decisions), with λ chosen on val from {1e-5, …, 0.1}.
- `book(residual(maia+elo))`: the opening book on top of it.

The baselines reproduce E4 exactly. ΔNLL is the mean difference with a 95%
game-clustered bootstrap CI (lower is better).

| | thibault | warpentak |
|---|---|---|
| maia NLL / top1 | 1.2322 / 0.589 | 1.5544 / 0.502 |
| residual NLL / top1 | 1.2194 / 0.592 | 1.5419 / 0.505 |
| residual vs maia, all | −0.0128 [−0.0159, −0.0097] | −0.0125 [−0.0165, −0.0085] |
| **residual vs maia, out-of-book** | **−0.0140 [−0.0173, −0.0106]** | −0.0042 [−0.0082, **+0.0002**] |
| residual vs maia+elo+T (paired), all | −0.0044 [−0.0075, −0.0017] | −0.0111 [−0.0147, −0.0072] |
| **residual vs maia+elo+T (paired), out-of-book** | **−0.0071 [−0.0102, −0.0041]** | **−0.0068 [−0.0107, −0.0027]** |
| book(residual) NLL / top1 | **1.1611 / 0.608** | **1.3533 / 0.566** |
| book(residual) vs book(maia+elo+T) (paired) | −0.0067 [−0.0097, −0.0039] | −0.0056 [−0.0090, −0.0023] |
| out-of-book top1, maia → residual | 0.584 → 0.587 | 0.525 → 0.529 |
| chosen λ / Elo offset | 1e-5 / +100 | 1e-5 / 0 |
| play T chosen on val, book(residual) | 0.8 → test 56 cp / 4.1% (actual 55 / 4.1%) | 0.8 → test 71 cp / 5.7% (actual 77 / 6.3%) |

Fit cost: 47 s and 32 s (featurization dominates), plus train analysis (cold, about
7 min per 1,000 games; 16 s cached). Prediction cost is under 1 ms per decision on
top of the cached analysis.

Largest learned weights (descriptive, relative to Maia; not psychological claims):
- thibault: wp_loss_low_clock −0.95, wp_loss +0.49, piece_pawn −0.36, castle +0.28,
  captured_value −0.27, check −0.21.
- warpentak: wp_loss_low_clock −0.49, captured_value +0.48, castle −0.44,
  promotion +0.42, capture −0.38, recapture −0.26.

**Conclusions (as measured, no stronger)**
1. **The personalized model is the best predictor for both players.** It
   significantly beats every baseline, including the strongest
   (`book(maia+elo+T)`), with paired CIs excluding 0.
2. **It learns something beyond repertoire.** Out-of-book it beats the tuned prior
   (maia+elo+T) for both players. Against plain Maia, the out-of-book gain is
   significant for thibault and borderline for warpentak, whose CI upper bound is
   +0.0002, so no claim is made there.
3. **The effect is small:** about 0.5–1.1% of NLL and +0.3–0.4 points top-1
   out-of-book. Repertoire (the book) remains the dominant player-specific signal,
   at −0.064 / −0.195 NLL. Larger gains need context (time control, opponent
   rating, recency; It. 6) or richer models. This is unmeasured.
4. **The play temperature for the personal model is 0.8 for both**, chosen on val.
   It reproduces thibault's move quality on test closely, and is slightly too
   strong for warpentak (71 vs 77 cp).
5. λ landed at the grid minimum twice, but the val NLL curve is flat there (Δ <
   0.0002 across 1e-5 … 3e-4), so the result is insensitive to it.

**Incident during the run.** The first train-split analysis of thibault crashed:
Stockfish 19 exits on a "From Position" start with seven white queens, which is
legal by the rules and valid per python-chess. Six such games exist (145 plies).
Fixed by making engine failures per-position (ARCHITECTURE D28). The engine
first-run race (EOF before exit status) needed a second fix. The final runs
report 0 engine-unavailable decisions in val/test.

## E5.2: Live personal-opponent latency (2026-10-23)

`scripts/bench_play.py --player thibault`: both sides are the PersonalOpponent of
thibault's profile, with **no caching** (prior + best search 5k nodes + set search
10k nodes + residual + book per move). Idle machine (load about 2).

| torch threads | games | plies | p50 | p95 | p99 | max | peak RSS (Python) |
|---|---|---|---|---|---|---|---|
| 1 | 6 | 548 | 49.5 ms | 57.3 ms | 69.3 ms | 127.6 ms | 278 MB |
| 4 | 4 | 320 | 50.4 ms | 66.5 ms | 70.7 ms | 72.4 ms | 272 MB |

The Stockfish processes come on top of that, with a 4 MB hash each. Through the
HTTP API the first moves took 27–39 ms (some cache hits).

Profile training (`pco train thibault`, 996 games): 431 s, mostly cached analysis
plus new prior calls at the Elo offset. Profile file: 2.1 MB. Validation (newest
100 games): NLL 1.160 vs Maia 1.233; top1 0.615 vs 0.590.

## E6.2 PRE-REGISTRATION (written 2026-10-23, before looking at any LonelySheperd ablation result)

Exploratory ablations on thibault and warpentak (E6.1, test splits inspected) suggest:
- **v2 = v1 + "phase" interaction group + recency weighting**
- half-life chosen on val from {None, 400, 200, 100, 50, 25} games
- λ chosen on val

**Confirmatory test:** on `LonelySheperd`, a third Lichess player with a mixed
blitz/rapid history, imported 2026-10-23 and not used for any design choice, v2
must beat v1 on the test split with a paired game-clustered 95% CI excluding 0
(ΔNLL < 0). The other variants are reported as exploratory only. The
time-control group is of special interest for this player but is not part of the
claim.

## E6.1: Context and recency ablations (2026-10-23, exploratory for thibault and warpentak)

Script: `scripts/ablate_personal.py --home H PLAYER`.
- Same 70/10/20 split as `pco eval`, with the base at the val-fitted Elo offset.
- Features are computed once.
- Each variant chooses λ ∈ {0.1 … 1e-5} and, where listed, the half-life on val.
- Test is used only for the paired game-clustered bootstrap vs v1 (the E5
  residual).
- v1 reproduces E5.1 exactly (thibault test NLL 1.2194, warpentak 1.5419).

ΔNLL vs v1 on test, overall [95% CI] (out-of-book in parentheses):

| variant | thibault | warpentak |
|---|---|---|
| +time_control | +0.0001 [−0.0003, +0.0005] | +0.0000 |
| +clock | +0.0003 [−0.0004, +0.0012] | −0.0006 [−0.0017, +0.0004] |
| +opponent | +0.0001 [−0.0000, +0.0002] | +0.0000 |
| +phase | −0.0013 [−0.0023, −0.0004] (−0.0006), but val worse | **−0.0055 [−0.0076, −0.0033]** (−0.0019) |
| +all | −0.0008 [−0.0022, +0.0004] | −0.0052 [−0.0073, −0.0030] |
| v1 + recency | **−0.0031 [−0.0047, −0.0017]** (−0.0035), h=100 | −0.0002, h=400 (out-of-book +0.0008) |
| +phase + recency | **−0.0044 [−0.0061, −0.0029]** (−0.0042), h=100 | **−0.0059 [−0.0081, −0.0037]** (−0.0009), h=400 |
| +all + recency | −0.0028 [−0.0048, −0.0007] | −0.0052 |

Fit times: 1.5–4 s per spec without recency; 8–26 s with the 6-point half-life grid;
up to 70 s for "+all+recency" (59 features). The h=100 optimum for thibault is
interior; 50 and 25 were not chosen.

## E6.2: Pre-registered confirmation on LonelySheperd: **FAILED**

Pre-registration text is above, written before the result. The player is
LonelySheperd: Lichess, 1,000 games, 552 blitz / 408 rapid / 40 bullet. Split
`ce02dba7162fb9df` is 660/93/188 games, with 2 duplicate move sequences dropped.
The val-chosen Elo offset is +200.

| variant | ΔNLL vs v1 (test) | out-of-book |
|---|---|---|
| **v2 = +phase+recency (pre-registered)** | **−0.0010 [−0.0026, +0.0007]**, n.s. | −0.0005 [−0.0022, +0.0012] |
| +time_control (mixed-TC player) | +0.0005 [−0.0005, +0.0015] | +0.0005 |
| +clock | +0.0004 | +0.0005 |
| +opponent | −0.0002 | −0.0001 |
| v1 + recency | 0 (val chose no weighting) | 0 |

**Conclusions**
1. The pre-registered claim "v2 > v1" is **not supported** on the independent
   player. Phase interactions gained on one player of three. The context groups
   (time control, clock bands, opponent rating) are null on all three, including
   the mixed blitz/rapid player.
2. Recency weighting, chosen on val, helped the player with the longest history
   (thibault, 602 days, h=100) and was effectively not selected for warpentak
   (316 days, h=400, ≈0) and LonelySheperd (121 days, none). That is consistent
   with "recency matters when behavior had time to change", but three players
   can't establish it.
3. **Decision:** the default model is v1 features plus a val-selected half-life
   (none allowed). Context groups remain opt-in research options (D29).

## E6.3: Deployable v2 (v1 features + val-chosen recency) via `pco eval` (2026-10-23)

| | thibault | warpentak | LonelySheperd |
|---|---|---|---|
| chosen half-life | 100 games | 400 | none |
| residual vs maia, out-of-book | **−0.0174 [−0.0211, −0.0139]** (E5: −0.0140) | −0.0034 [−0.0076, +0.0012] | **−0.0153 [−0.0216, −0.0092]** |
| residual vs maia+elo+T (paired), out-of-book | **−0.0106 [−0.0139, −0.0075]** | **−0.0060 [−0.0100, −0.0016]** | **−0.0098 [−0.0140, −0.0051]** |
| book(residual) NLL / top1 | **1.1580 / 0.609** (E5: 1.1611 / 0.608) | 1.3541 / 0.566 | **1.1282 / 0.625** |
| book(residual) vs book(maia+elo+T) (paired) | **−0.0098 [−0.0129, −0.0069]** | **−0.0048 [−0.0085, −0.0014]** | **−0.0086 [−0.0124, −0.0048]** |

LonelySheperd baselines (first measured here): maia 1.2258 / 0.595; maia+elo
(+200) 1.2183; book(maia+elo+T) 1.1367 / 0.619; 834 of 6,750 test decisions
in-book. The personal model beats the strongest baseline for **all three** players,
and beats the tuned prior out-of-book for all three. Against plain Maia
out-of-book it is significant for 2 of 3.

## E6.4: Training speed and incremental updates (2026-10-23)

Profiling a fully cached `train_profile` (thibault, 34k decisions) under cProfile:
`Maia3Prior.input_key` took about 46% of the time (1.07M FEN generations for
8-position history keys), `DecisionEpisode.board()` about 24% (272k
reconstructions with legality re-checks), and the 13-point Elo grid re-ran the
prior on every new val set. Fixes (D31):
- unchecked pushes of verified moves
- a memoized `board()`
- a 1-FEN `input_key` ("k2" format)
- a coarse-to-fine Elo search (7 passes)
- an Elo warm start from the previous profile (3 passes when stable)

Featurization alone went 6.9 s → 3.7 s per 6,000 decisions. **Results are
unchanged:** `pco eval thibault` after the changes reproduced every predictor's
test metrics to 1e-9, the Elo offset (+100) and the quality sweep exactly. That
run took 784 s, because the prior cache had to be rebuilt once under the new key
format.

Honest incremental test (`h12`, cold cache, Lichess thibault): train on 250
games, then import the 50 newest games for real and retrain.

| step | wall | engine searches computed / cached | prior evals computed / cached |
|---|---|---|---|
| train, 250 games (8,877 decisions), cold | 292 s | 16,879 / 1,757 | 22,809 / 4,034 |
| **train after +50 new games** (before the warm start: 153 s) | **79 s** | 3,691 / 18,737 | 4,803 / 22,224 (was 9,303) |
| train again, nothing new | 15 s (was 24 s) | 0 / 22,428 | 0 / 27,027 |

Behavior-change detection on real data (`pco train`, 950 games of thibault):
"change detected". The newer-half model is better on the newest games by +0.0081
NLL [+0.0047, +0.0112]. The largest weight shift is engine_best +0.16 (in the
newer half, the engine's best move is more often the one chosen). This agrees
with recency helping thibault only. On 250–300-game profiles, the 25–30
validation games are below the 30-game minimum, so it reports "insufficient data".

## E7.1: Does the opening book benefit from the whole history? (2026-10-24)

Script: `scripts/book_history.py --home H PLAYER --recent 2000`. It ran on the
owner's own data: about 9.5k games over 5 years. Only engineering conclusions are
recorded here, no personal statistics.
- Holdout: the newest 200 games of the 2,000-game window. Alpha is chosen on the
  older half of the holdout and the variants are reported on the newer half
  (3,034 decisions).
- Base: Maia at the val-fitted Elo offset.

| book counts from | ΔNLL vs recent-window book [95% CI] |
|---|---|
| recent window (1,800 training games) | reference |
| whole history, unweighted | **+0.0270 [+0.0204, +0.0332]** (worse) |
| whole history, half-life 1,000 games | −0.0012 [−0.0033, +0.0009] |
| whole history, half-life 400 games | −0.0014 [−0.0052, +0.0024] |

In-book coverage of the holdout rose only from 13.3% to 14.3% with the whole
history. **Conclusion:** old repertoire misleads when openings change over years,
and recency-weighted old games add nothing measurable. Training defaults to the
2,000 most recent games (D33). This is one player; it would need confirming on
more long-history players.

## E7.2: Speed presets: accuracy vs cost (2026-10-24)

`pco eval PLAYER --speed {lite,standard,deep}` on the same splits as E4–E6. The
prior cache is shared across presets; engine work is recomputed per budget.

| player | final model NLL, lite / standard / deep | paired vs book(maia+elo+T), lite |
|---|---|---|
| thibault | 1.1585 / 1.1580 / 1.1587 | −0.0092 [−0.0123, −0.0064] |
| warpentak | 1.3538 / 1.3541 / — | −0.0051 [−0.0084, −0.0019] |
| LonelySheperd | 1.1291 / 1.1282 / — | −0.0076 [−0.0113, −0.0040] |

Out-of-book vs maia+elo+T stays significant at Lite for all three: −0.0096,
−0.0060 and −0.0087. Analysis throughput (4 engine workers):

| preset | budget (k_max, mass, set/best nodes) | decisions/s measured |
|---|---|---|
| lite | 3, 0.90, 3k/1.5k | 115–120 (idle machine, thibault); 67–70 (with concurrent load) |
| standard | 5, 0.95, 10k/5k | 44–58 |
| deep | 6, 0.97, 30k/15k | 37–39 |

A full Deep eval of thibault took 2,575 s, vs 417 s for Lite (both with the prior
already cached). **Conclusion:** deeper analysis doesn't improve imitation
accuracy for this model, so **Lite is the default** (D32). Standard and Deep remain
available. The quality-sweep cp numbers shift slightly with depth, because the
engine reference changes; the chosen play temperature stayed at 0.8.

## E7.3: Onboarding run, time estimate, Lite live latency (2026-10-24)

- `pco quickstart --lichess warpentak --recent 300` into an empty home took 352 s
  end to end: setup check, an import of 3,347 games (about 2 min), a Lite training
  on 9,405 decisions in 209 s, and the ready message.
- Total training throughput is about 0.6× the analysis throughput: 45 vs about 70
  decisions/s here, and 25 vs about 44 on the owner's 66k-decision Standard run.
  The quickstart estimate uses that factor.
- Live latency of a Lite-trained personal opponent (`bench_play.py`, 1 thread, no
  cache): p50 38.7 ms, p95 50.2 ms, peak RSS 268 MB (Standard: about 50 ms, E5.2).
