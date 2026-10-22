# PROJECT — Personal Chess Opponent (`pco`)

Source of truth for intended behavior: [`docs/MASTER_SPEC.md`](../docs/MASTER_SPEC.md)
(the master prompt, preserved verbatim). This file summarizes it; on conflict, the
spec wins unless a documented decision in ARCHITECTURE.md says otherwise.

## Vision

**Import your chess games → learn how you play → play against yourself.**

Approximate one player's move-selection distribution,
`P(move | position, history, game context, time constraints)`, from their own
historical games, and let them play against a stochastic sample of it.

It is NOT a strength engine, a Stockfish wrapper, a rating-matched bot, or a
hand-scored personality classifier.

## Core architecture (conceptually separate and replaceable)

| Role | Component |
|---|---|
| Human-move prior | Maia-family pretrained model (chosen: Maia-3 5M) |
| Player personalization | Lightweight learned residual over the prior |
| Objective evaluator | Stockfish (UCI subprocess), never used as a human prior |
| Evidence | Local database of the player's real games |
| Temporal/context | Recency, time control, clock pressure (learned, validated) |

Optimization target: **behavioral similarity per CPU-second**.

## Non-negotiables

- CPU-only and local-first. No GPU, no cloud inference or training, no required server.
  Network use is allowed only for optional account imports and one-time asset downloads.
- Do not train Maia from scratch. Do not substitute Stockfish for the human prior.
- Personalization is learned from behavioral evidence. Hand-assigned style scores are
  allowed only as explanations or diagnostics.
- Start with the simplest model and add complexity only when measurements justify it.
- Layered local data: raw → decision → derived. Derived data must be reproducible.
- Sample stochastically from a calibrated distribution. Don't always play the top move.
- Evaluate on held-out games split chronologically. Claim no improvement without
  beating baselines.
- Self-play is never treated as human evidence.

## Primary user experience

1. Import games (PGN file, Lichess account, Chess.com account).
2. Process history (decision episodes and analysis, cached and incremental).
3. Learn a personal profile.
4. Play against it locally, with a player-likeness score that is not presented as intent.

## Out of scope

General chess platform features, analytics dashboards, online play, cloud accounts,
training a human prior, and strength optimization. "Future Research" items in the spec
(embeddings, era detection, Peak Me, and so on) wait until the core product works.

## Constraints and assumptions (Iteration 0)

- Reference dev machine: Intel i5-1235U (10 cores, 12 threads), 15 GB RAM, Linux, no GPU.
- Python 3.12 is pinned (`requires-python >=3.12,<3.13`). uv provisions it, because
  the system Python is 3.14.
- **Open product decision, licensing.** Maia-3 code and weights are AGPL-3.0 and run
  in-process, so if the project is distributed it must be AGPL-compatible (e.g.
  AGPL-3.0-or-later). The MIT-licensed alternative is Maia-2. It measured 4.8 points lower in
  top-1, has NLL higher by 0.17, and its weights are 13x larger (EXPERIMENTS.md,
  E0.1). It would fit behind the same `HumanPrior` interface. The project license is not chosen yet;
  the owner should decide before any public release. This does not block local
  development.
