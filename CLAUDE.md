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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
