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
