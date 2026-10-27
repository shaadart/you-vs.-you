# You vs. You — Master Implementation Specification

> Preserved verbatim from the initial project prompt (received 2026-10-23).
> This file is the source of truth for intended product behavior. Do not edit
> its requirements; record deviations and their justification in
> `AI_CONTEXTS/ARCHITECTURE.md` / `AI_CONTEXTS/ROADMAP.md` instead.

---

## 1. Your Role and Mission

You are the principal software engineer, ML engineer, and technical architect responsible for building **You vs. You**.

Your task is to translate the provided engineering and research specification into a working, local-first application through carefully planned, manageable development iterations.

**Core mission:**

Build a computationally efficient chess opponent that learns an individual player's observable move-selection behavior from their historical games, allowing the player to compete against an approximation of their own playing style.

The intended experience is:

**Import your chess games → Learn how you play → Play against yourself.**

This is not a conventional chess engine, a Stockfish wrapper, a rating-matched bot, or a generic personality classifier.

The system must approximate the probability distribution of moves that a particular player might select in a given chess position and context.

Conceptually:

`P(player move | board position, player history, game context, time constraints)`

The central architecture is:

- **Maia:** A pretrained human-chess prior that estimates plausible human moves.
- **Player personalization:** A lightweight learned component representing how this specific player differs from the human prior.
- **Stockfish:** An objective evaluator of chess positions and candidate-move consequences.
- **Historical game database:** The source of actual player decisions.
- **Temporal/contextual model:** Accounts for changing behavior, playing conditions, and time controls.

These responsibilities must remain conceptually separate and replaceable.

The system must prioritize **behavioral similarity per CPU-second**, not playing strength.

---

## 2. Mandatory Development Strategy

Do not attempt to implement the entire specification in one pass.

Instead, develop the project through **small, independently verifiable iterations**.

Each iteration must:

1. Have one clear primary objective.
2. Build on the previously completed iterations.
3. Produce concrete, working deliverables.
4. Include relevant tests and validation.
5. Preserve architectural compatibility with future requirements.
6. Update project documentation and persistent context.
7. End at a stable checkpoint before the next iteration begins.

Avoid creating large amounts of speculative code for future features.

**Implement what the current iteration needs while designing interfaces that permit future expansion.**

Do not sacrifice architectural integrity for speed, but do not overengineer.

Prefer simple, maintainable, modular solutions with clear responsibilities.

### Execution rule

**Work on only one iteration at a time.**

Do not automatically proceed through the entire roadmap.

At the start of each session:

- Inspect the repository.
- Read the persistent context from `AI_CONTEXTS/`.
- Identify the current iteration and its completion state.
- Determine the next required work.
- Continue from the actual project state.

At the end:

- Run appropriate validation.
- Update context files.
- Summarize the deliverables.
- Identify remaining issues.
- State the next iteration.
- Stop at the iteration boundary.

When I say **"continue"**, resume from the existing context and complete the next appropriate iteration without rebuilding the roadmap from scratch.

If an iteration is too large, divide it into smaller, independently testable substeps, without changing the overall objectives.

---

## 3. Persistent Context System — `AI_CONTEXTS/`

Create and maintain a folder named exactly:

`AI_CONTEXTS/`

This is mandatory.

It is the persistent engineering memory of the project, designed to survive context resets, session changes, and handoffs to another coding agent.

Maintain the following structure:

```text
AI_CONTEXTS/
├── PROJECT.md
├── ARCHITECTURE.md
├── ROADMAP.md
├── PROGRESS.md
├── EXPERIMENTS.md
└── HANDOFF.md
```

### PROJECT.md

Maintain the project's fundamental identity:

- Product vision and core objective.
- Non-negotiable requirements.
- Primary user experience.
- In-scope and explicitly out-of-scope functionality.
- Critical research and engineering constraints.
- Reference to the original master specification.

Treat the original specification as the source of truth for intended product behavior. Preserve it separately if supplied as a repository file.

### ARCHITECTURE.md

Document:

- Actual technology stack.
- System components and interfaces.
- Data flow.
- Storage architecture.
- Model and inference architecture.
- Dependencies and integration boundaries.
- Important technical decisions and their reasoning.
- Known architectural limitations.

Document the architecture that actually exists, clearly distinguishing planned components from implemented components.

Update this file whenever an architectural decision materially changes.

### ROADMAP.md

Maintain:

- Ordered development iterations.
- Scope and deliverables for each iteration.
- Acceptance criteria.
- Dependencies between iterations.
- Current iteration.
- Completed, pending, and blocked work.

Use simple statuses:

`PENDING / IN_PROGRESS / COMPLETED / BLOCKED`

Do not mark an iteration completed until its acceptance criteria are verified.

### PROGRESS.md

Maintain a concise chronological development log:

- Work completed.
- Files or modules added or changed.
- Tests executed and their results.
- Bugs resolved.
- Known limitations.
- Important decisions.
- Blockers and unfinished work.

Do not use vague statements such as "backend mostly finished."

Record observable facts and concrete results.

### EXPERIMENTS.md

Document technical research and measurable results, including:

- Model and dependency feasibility.
- CPU and memory benchmarks.
- Training and inference experiments.
- Evaluation datasets and splits.
- Baseline performance.
- Personalization performance.
- Calibration and accuracy measurements.
- Unsuccessful approaches and their lessons.

Never invent experimental results or claim that a model improves performance without measurement.

### HANDOFF.md

This is the most important document for session continuity.

Keep it concise and immediately actionable.

It must explain:

- Current implementation state.
- Current iteration and precise stopping point.
- Last known working functionality.
- What remains unfinished.
- Current blockers.
- Relevant files and commands.
- Tests that should be run.
- Exact next development action.

**Update `HANDOFF.md` at the end of every iteration and before ending any interrupted session whenever possible.**

A fresh Claude Code session should be able to read `HANDOFF.md`, then the other relevant context files, and continue without needing the entire conversation history.

### Context management rules

- Never erase useful decisions or experimental evidence.
- Never rewrite completed history to make progress appear cleaner.
- Never record planned work as implemented work.
- Keep context accurate, concise, and useful.
- Update only the documents affected by actual changes.
- Avoid duplicating the same information across multiple context files.
- Repository code and passing tests are the implementation truth; context documents must accurately reflect them.

---

## 4. Non-Negotiable Engineering Constraints

### CPU-first and local-first

The complete core experience must function on ordinary consumer computers without a dedicated GPU.

Do not require:

- CUDA or GPU hardware.
- Cloud inference.
- Cloud training.
- A continuously running remote server.
- A constant internet connection.

Internet access is acceptable for optional account imports or initial dependency/model downloads, but locally imported PGNs and the core playing experience must work offline once required assets are installed.

Optimize for low memory usage, efficient CPU inference, incremental processing, caching, and fast reuse of existing computations.

### Model architecture

Do not train Maia from scratch.

Investigate an existing pretrained Maia-family model or a technically suitable, properly licensed equivalent.

Verify actual model compatibility, availability, licensing, and practical CPU performance before committing to an integration.

Do not substitute Stockfish for Maia and pretend it is a human-move prior.

Do not assume the strongest engine move is the player's preferred move.

### Personalization

The primary player representation must be learned from real behavioral evidence.

Do not build the central move-selection mechanism around arbitrary hand-assigned scores such as aggression, creativity, or patience.

Interpretable behavioral statistics are useful for explanations and diagnostics, but they are not a substitute for a learned decision policy.

Start with the simplest effective personalization model.

Only introduce embeddings, nonlinear models, or more advanced architectures when experiments justify the added complexity.

### Privacy and storage

Player data, game histories, analysis, and derived profiles should remain local by default.

Use a layered data architecture:

1. **Raw data:** Original games and metadata.
2. **Decision data:** Normalized decisions and extracted features.
3. **Derived data:** Trained player profiles, representations, and statistics.

Derived data must remain reproducible.

### Maintainability

Prefer:

- Clear module boundaries.
- Typed interfaces where practical.
- Reusable components.
- Explicit configuration.
- Automated tests.
- Stable data schemas.
- Versioned caches and model artifacts.
- Minimal dependencies.

Avoid premature microservices, distributed infrastructure, unnecessarily complicated frameworks, and speculative abstractions.

Use the existing repository stack when appropriate. If beginning from scratch, choose the smallest practical stack and explain the decision.

---

# 5. Development Roadmap

The following iterations define the intended implementation sequence.

You may adjust iteration boundaries when technical evidence warrants it, but preserve the dependencies, product requirements, and measurable acceptance criteria.

## Iteration 0 — Repository Assessment, Feasibility, and Foundation

**Goal:** Establish a technically credible implementation plan before building complex functionality.

Tasks:

- Inspect the existing repository and identify reusable work.
- Read and understand the complete master specification.
- Choose or confirm the technology stack.
- Define major architectural boundaries.
- Investigate practical Maia model availability, licensing, inference format, and CPU feasibility.
- Investigate Stockfish integration requirements.
- Establish the testing and configuration strategy.
- Define the minimal initial database architecture.
- Identify material technical risks and uncertain assumptions.
- Create the `AI_CONTEXTS/` documentation system.

Where feasible, run a small dependency or inference feasibility test instead of relying solely on documentation.

**Deliverables:**

- Repository foundation and development setup.
- Initial architecture.
- Populated context files.
- Feasibility findings with unresolved risks clearly identified.
- A realistic implementation roadmap.

**Acceptance:**

The project can be initialized consistently, its dependencies and feasibility constraints are understood, and the next iteration is implementable without major architectural guessing.

Do not spend this iteration building an unnecessarily elaborate application skeleton.

---

## Iteration 1 — Game Import and Data Foundation

**Goal:** Reliably convert historical chess games into structured local data.

Implement:

- PGN importer.
- Chess.com importer.
- Lichess importer.
- Source-independent game normalization.
- Legal move replay and validation.
- Player identity and color identification.
- Available game metadata extraction.
- Local persistent storage.
- Duplicate detection and safe re-import.
- Preservation of original game data.

Support relevant metadata when available:

- Date and platform.
- Ratings.
- Time control.
- Clock information.
- Game result.
- Player color.
- Move sequence.

Use official APIs or exports wherever practical. Keep external platform dependencies behind importer adapters.

Handle missing or malformed information gracefully rather than fabricating it.

**Deliverable:**

A functional local import pipeline that converts PGNs and supported platform exports into consistent, queryable chess-game records.

**Acceptance:**

Realistic test fixtures import successfully, invalid inputs are handled, games can be reconstructed accurately, and repeated imports do not corrupt or unnecessarily duplicate data.

---

## Iteration 2 — Playable Chess Core and Human Prior

**Goal:** Establish the smallest playable application and a genuine human-move baseline.

Implement:

- Chess board state management.
- Legal move generation and validation.
- Game progression and termination.
- Minimal playable local interface.
- A defined model-inference interface.
- Maia or the validated human-prior integration.
- CPU inference and probability-based move selection.
- Reliable handling of model errors and unsupported positions.

The interface should remain deliberately simple.

Avoid building a large chess platform or analytics dashboard.

If Maia integration is blocked, clearly document the blocker and isolate any temporary fallback behind the same interface. Never present a fallback as Maia or as a personalized player.

**Deliverable:**

A locally playable chess application capable of selecting legal moves from a human-oriented move policy when the required model is available.

**Acceptance:**

A complete legal game can be played. Model outputs are valid, inference works on the intended CPU setup, and basic memory/latency measurements are recorded.

---

## Iteration 3 — Decision Episodes, Features, and Analysis

**Goal:** Transform historical games into a behavioral learning dataset.

Develop a `DecisionEpisode` representation.

A decision episode should capture, where available or computationally justified:

- Position before the player's move.
- Legal moves and actual chosen move.
- Player identity and color.
- Game metadata and rating context.
- Time control and clock context.
- Game phase and relevant positional features.
- Bounded recent game history.
- Candidate move set.
- Maia move probabilities.
- Stockfish candidate evaluations.
- Candidate-level decision features.
- Temporal information.
- Missing-data indicators.

Do not implement every speculative feature immediately.

Prioritize features that plausibly alter action selection and that can also be computed during live inference.

Implement:

- Candidate generation from the human prior and useful chess alternatives.
- Stockfish evaluation adapter.
- Basic strategic and tactical feature extraction.
- Stable caching of expensive computations.
- Adaptive analysis limits.
- Separation of observed inputs from derived analysis.

The player's actual historical move must be included when constructing training examples, but it must not be artificially inserted as a known choice during live inference.

Protect against data leakage. In particular, future moves, eventual game results, and information unavailable at decision time must not become predictive inputs.

**Deliverable:**

A reproducible pipeline producing decision-level training and evaluation data.

**Acceptance:**

Historical decisions can be replayed, normalized, and analyzed consistently. Caches operate correctly, missing metadata is handled, and features respect inference-time availability.

---

## Iteration 4 — Evaluation Framework and Baselines

**Goal:** Make behavioral reconstruction objectively measurable before implementing advanced personalization.

This iteration is mandatory.

Build an evaluation framework based on held-out real human games.

Use chronological separation, keeping entire games together and preventing duplicate-game leakage across training and testing.

Evaluate at least:

- A simple non-personalized baseline.
- Generic Maia.
- Available simple historical or player-statistical baselines.

Prepare the framework for comparing future learned and temporal models.

Measure:

- Top-1 move accuracy.
- Top-k accuracy.
- Actual-move log likelihood.
- Probability calibration.
- Inference performance.
- Relevant contextual and temporal breakdowns.

Account for candidate-set truncation and nonzero probability coverage so the evaluation is mathematically meaningful.

Reduce opening memorization bias and distinguish improvements caused by repeated opening positions from broader behavioral generalization.

**Deliverable:**

A repeatable evaluation command and recorded baseline results.

**Acceptance:**

The evaluation can be reproduced using fixed data splits, settings, and model versions.

Do not claim personalization success before it beats appropriate baselines on held-out decisions.

---

## Iteration 5 — Personalized Move Selection V1

**Goal:** Produce the first genuine player-specific opponent.

Implement a lightweight personalization layer that adjusts the pretrained human move distribution using evidence from the player's historical decisions.

Start by testing simple approaches such as:

- Regularized logistic models.
- Candidate-ranking models.
- Compact calibrated rerankers.
- Small learned player-specific parameters.

Do not jump immediately to complex neural architectures.

The personalized policy should learn systematic deviations from the generic human prior.

Requirements:

- Proper probability normalization.
- Legal move guarantees.
- Stochastic sampling rather than always selecting the highest-probability move.
- CPU-efficient inference.
- Reproducible training.
- Persistent model artifacts.
- Validation on held-out real decisions.

Keep explanatory behavioral statistics separate from the actual move-selection mechanism.

Introduce a player-likeness score based on the model's estimated probability of a decision, but avoid presenting this as verified psychological intent.

**Deliverable:**

The first opponent that learns from an imported player's games and can be played against locally.

**Acceptance:**

The entire workflow works end to end:

**Import → Build decision data → Train personal profile → Play personalized opponent → Evaluate.**

Report whether personalization improves held-out prediction over generic Maia. If it does not, preserve the working implementation and investigate the failure rather than claiming success.

---

## Iteration 6 — Context, Temporal Behavior, and Incremental Learning

**Goal:** Improve how the model represents the player's current behavior and changing playing conditions.

Implement, based on available evidence and measured benefit:

- Historical and current player representations.
- Recency-sensitive learning.
- Time-control conditioning.
- Clock-pressure conditioning where data exists.
- Contextual player behavior.
- Incremental profile updates after importing new games.
- Selective reuse of cached analysis.
- Basic behavioral change detection.

Preserve historical games and previous model information.

Do not assume arbitrary fixed calendar periods correspond to meaningful behavioral eras.

Do not create excessively fragmented profiles when available data is sparse.

The model should support shared player identity with contextual adaptations.

Do not treat generated self-play games as equivalent to real human evidence.

Evaluate whether temporal and contextual conditioning genuinely improves future-game prediction.

**Deliverable:**

A player representation that can update efficiently from new games and account for relevant changes in behavior.

**Acceptance:**

New games can update the profile without unnecessarily rebuilding all expensive analysis, and temporal/contextual benefits or limitations are measured.

---

## Iteration 7 — Product Integration, Optimization, and Release Readiness

**Goal:** Turn the working research-driven implementation into a coherent local application.

Refine the complete user workflow:

1. Import your games.
2. Process your chess history.
3. Learn your playing behavior.
4. Build your personal opponent.
5. Play against yourself.

Implement and test practical hardware profiles:

**Lite:** Low-end CPU, smaller candidate sets, minimal expensive computation.

**Standard:** Balanced CPU performance and behavioral fidelity.

**Deep:** Optional increased analysis depth and computational budget.

Keep the underlying personalized policy architecture consistent.

Improve:

- Usability and onboarding.
- Import and analysis progress reporting.
- Error recovery.
- Startup and inference performance.
- Memory usage.
- Model and cache versioning.
- Incremental processing.
- Local installation and deployment.
- Robustness across supported platforms.
- Documentation and final tests.

Avoid unnecessary dashboards or unrelated product features.

**Deliverable:**

A usable, documented, local-first application with an end-to-end personalized chess experience.

**Acceptance:**

The core user journey works reliably on supported CPU hardware, with clear performance measurements, reproducible evaluation, and an honest description of remaining model limitations.

---

## Future Research — Only After the Core Product Works

Preserve architectural room for:

- Compact learned player embeddings.
- More sophisticated personalized ranking models.
- Better behavioral era detection.
- Current Me vs Historical Me.
- Time-control-specific player variants.
- Peak Me or historical rating-based variants.
- Improved game-trajectory modeling.
- Long-term generated-game behavioral comparisons.
- Blind human evaluation of real vs generated playing styles.

These are not requirements for the initial functional product.

Investigate them only when the baseline system is operational and experiments indicate a meaningful opportunity for improvement.

Do not expand the project into a general chess platform.

---

## 6. Research and Modeling Rules

Throughout development, follow these principles.

### Learn decisions, not just played moves

The model should learn why one available action is statistically preferred over plausible alternatives, using observable evidence.

Represent candidate alternatives and their consequences whenever computationally justified.

### Avoid false personality signals

Repeated memorized opening moves should not dominate the inferred playing style.

Distinguish opening familiarity from generalizable move preferences as far as the evidence permits.

### Model uncertainty

Forced positions offer less behavioral choice than positions containing several plausible alternatives.

Investigate uncertainty and information-based decision weighting, but validate whether the additional complexity helps.

### Respect temporal evidence

Retain historical information while prioritizing relevant current behavior.

Do not simply delete older games or assign permanent arbitrary recency weights without validation.

### Avoid synthetic feedback loops

Actual user games should remain the primary evidence.

Generated self-play must not continually retrain the player's personality into a self-reinforcing synthetic approximation.

### Preserve stochastic behavior

Sampling from a calibrated learned distribution is essential.

The goal is to approximate how the player distributes choices, not always select one predicted move.

### Measure before optimizing

Use experiments to decide whether adding features, larger models, extra engine depth, or more complicated architectures is worthwhile.

Never assume complexity guarantees higher behavioral fidelity.

---

## 7. Code and Quality Standards

For every implementation iteration:

- Reuse existing project conventions where reasonable.
- Keep modules small and cohesive.
- Avoid duplicated logic.
- Handle common failures and missing data.
- Provide appropriate automated tests.
- Ensure chess move legality and state consistency.
- Make training and evaluation reproducible.
- Avoid hardcoded machine-specific paths.
- Keep configuration explicit.
- Do not silently introduce cloud dependencies.
- Keep expensive computation bounded and cancellable where practical.
- Preserve compatibility with existing persisted data or provide a migration.
- Do not fabricate test results or silently ignore failures.

When a proposed feature is scientifically uncertain, implement the smallest useful experiment before committing to a complex solution.

When a requirement is blocked by external dependencies, record the exact issue, maintain a clean integration boundary, and progress with independent work where possible.

Do not hide incomplete behavior behind mock results presented as real.

---

## 8. Iteration Completion Protocol

At the end of every iteration:

### A. Verify

Run all relevant checks:

- Unit tests.
- Integration tests where applicable.
- Type checking or linting when configured.
- Relevant functional workflows.
- Model evaluation when applicable.
- CPU performance checks when relevant.

Record actual results.

If something fails, fix it within the iteration where practical or record a clear blocker.

### B. Update `AI_CONTEXTS/`

Update the relevant architecture, roadmap, progress, experiment, and handoff documents.

Ensure they reflect the actual repository state.

### C. Report

Provide a concise completion summary:

**Iteration:** Name and number.

**Delivered:** What now works.

**Validation:** Tests and observed results.

**Decisions:** Significant technical choices.

**Limitations:** Anything unfinished or blocked.

**Next:** The next iteration's objective.

Avoid lengthy explanations of routine implementation details unless they affect important decisions.

### D. Stop

Once the iteration has met its acceptance criteria and the context is saved, stop.

Do not silently begin implementing future iterations.

The next session should be able to resume using the context files without requiring additional explanation from me.

---

## 9. Initial Instructions — Start Here

On receiving this prompt:

1. Inspect the current repository and any accompanying master specification.
2. Identify what already exists instead of assuming the project is empty.
3. Understand the product's core research objective and technical constraints.
4. Establish the `AI_CONTEXTS/` system.
5. Prepare the implementation roadmap, adapting it only where the repository or feasibility findings justify changes.
6. Begin **Iteration 0**.
7. Complete its deliverables and validation.
8. Update all relevant persistent context.
9. Report the outcome and stop.

Do not begin building advanced personalization models before verifying the foundations they depend on.

Do not ask for confirmation on routine implementation choices that can be resolved through sound engineering judgment.

Ask questions only when an essential product decision genuinely cannot be resolved from the specification, repository, or reasonable documented assumptions.

## Final Guiding Principle

Every major design decision should be evaluated against this question:

**Does this help us reconstruct this particular player's observable chess decisions more accurately, efficiently, and reliably?**

If not, it is probably unnecessary.

The system should favor:

**Strong pretrained human prior + high-quality player data + lightweight personalization + intelligent computation + rigorous validation.**

Not:

**Large models + excessive infrastructure + opaque heuristics + unmeasured complexity.**

Build the simplest genuinely effective personal chess opponent, one verified iteration at a time.

**Begin with Iteration 0 now.**
