# CLAUDE.md

You vs. You (`pco`): a local-first, CPU-only chess opponent that learns one
player's move distribution. It combines the Maia-3 prior, learned personalization,
and Stockfish as an evaluator.

- **Start every session with `AI_CONTEXTS/HANDOFF.md`**, then ROADMAP.md and
  ARCHITECTURE.md. The spec is `docs/MASTER_SPEC.md`.
- Work one iteration at a time and stop at the iteration boundary. Update
  `AI_CONTEXTS/` at the end of every iteration.
- Never record unmeasured results or planned work as done.
- Commands: `uv sync`, `uv run pco setup`, `uv run pco doctor`, `uv run pytest -q`,
  `uv run ruff check . && uv run ruff format --check .`
- Python 3.12 via uv. Never use the system Python.
