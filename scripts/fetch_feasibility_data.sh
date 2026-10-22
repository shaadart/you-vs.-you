#!/usr/bin/env sh
# Re-download the public Lichess arena games used by the Iteration 0 benchmarks
# (EXPERIMENTS.md E0.1-E0.3). Output: $1/arena_<id>.pgn. Data is not committed.
set -eu
out="${1:?usage: fetch_feasibility_data.sh OUT_DIR}"
mkdir -p "$out"
for t in 1nLI7ZTS CpEqUfrh; do  # "<=2000 Rapid Arena" 10+0, "<=1300 Blitz Arena" 5+0 (2026-10-22)
  curl -sf -H "Accept: application/x-chess-pgn" \
    "https://lichess.org/api/tournament/$t/games?clocks=true&evals=false&opening=true" \
    -o "$out/arena_$t.pgn"
  echo "$t: $(grep -c '^\[Event ' "$out/arena_$t.pgn") games"
done
