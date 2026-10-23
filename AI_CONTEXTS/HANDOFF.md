# HANDOFF

_Last updated: 2026-10-23, end of Iteration 4._

## State

- **Iterations 0–4: COMPLETED.** **Next: Iteration 5, personalized move selection
  V1 (PENDING, not started).**
- Working commands:
  - `pco import ...`, `pco games --verify`
  - `pco play` (generic Maia-3, T=0.7)
  - `pco build PLAYER`
  - `pco eval PLAYER`: chronological 70/10/20 split, baselines, bootstrap CIs,
    in/out-of-book, sampled-quality sweep, and a JSON report.
- `uv run pytest -q` passes 109.
- Baselines recorded (EXPERIMENTS E4.1/E4.2):
  - thibault: maia NLL 1.232 / top1 0.589; best baseline book(maia+elo+T) 1.168
    / 0.606; out-of-book 1.239 / 0.584.
  - warpentak: maia 1.554 / 0.502; book 1.359 / 0.562; out-of-book 1.447 /
    0.525.
- Real-data scratch homes (not user data, may vanish):
  `/tmp/claude-1000/-home-shaad-temp-projects-your-personal-chess-engine/991b25e7-2cec-4302-beab-73c2cc37c39c/scratchpad/h8` (thibault, 1,000 games, caches built for val+test) and `.../scratchpad/h9`
  (warpentak). Recreate with `pco import lichess NAME --max 1000` into a fresh
  `--home`, symlinking `assets` from `~/.local/share/pco/assets`.

## Read next

1. ROADMAP.md "5 — Personalized move selection V1": the model sketch and the
   "Inputs from It. 4" bullet list (the success bar).
2. EXPERIMENTS E4 findings 1–5.
3. `src/pco/evaluation/predictors.py`: the `Predictor` protocol that the
   personalized model must implement. See `Book` for composition.
4. `src/pco/decisions/features.py`: the candidate features the residual model can
   use.

## Exact next actions (Iteration 5)

1. Build train-split analyses too. They are needed for training features
   (`DecisionBuilder` on `split.train`; about 7 min per 1,000 games cold).
2. `src/pco/personal/model.py`: residual conditional logit over the live
   candidate set, `logit(m) = log P_prior(m) + w·φ(m, s)`.
   - Use a small feature set first: in-live-set, prior rank, cp_loss/wp_loss
     buckets, capture, check, castle, piece type, advance, phase interactions,
     and clock pressure × cp_loss.
   - Non-candidates keep prior mass.
   - L2 toward 0, with strength chosen on val NLL.
   - Fit with numpy (Newton/L-BFGS by hand or plain gradient). No new
     dependency unless justified.
3. Wrap it as `Book(Residual(EloOffset))` implementing `Predictor`, and add it to
   `make_predictors`. Report vs baselines on the same split fingerprints.
4. Profile artifact: weights, feature version, prior id, Elo offset, NLL T, play
   T (quality-matched on val), and the training data fingerprint. Save under
   `$PCO_HOME/profiles/`.
5. `PersonalOpponent` for `pco play --player NAME`. Live inference needs the
   engine (live candidate set), budgeted by `AnalysisConfig`. Show a
   player-likeness score: probability under the personal model, labeled as a
   model estimate.
6. Honest reporting: if out-of-book doesn't improve with a CI excluding 0, say so
   and investigate. Don't claim success.

## Watch out for

- Evaluate only via `pco eval`'s splits, and never tune on test.
- The temperature for play differs per player (0.7 vs 1.0). Fit it, don't hard-code.
- Train features for out-of-set actual moves are `separately_evaluated`; check
  that the model can't exploit that flag. It is unavailable live, so exclude it
  from features.
- `in_live_set` is also a training artifact for the actual move. Live
  distributions only score the live set plus prior mass elsewhere.
- Python is pinned to 3.12. Main-DB changes need migration v2+ (profiles may need
  a table). The license is still undecided.

## Commands

```sh
uv sync && uv run pco setup && uv run pco doctor
uv run pco import lichess USER --max 1000
uv run pco eval USER                       # ~15-20 min cold per 1k games, ~2.5 min cached
uv run pco build USER [--export f.jsonl]
uv run pco play [--elo 1500] [--temperature 0.7]
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```
