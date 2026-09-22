# Implementation Plan — Remaining New Features

**Status:** In progress (September 22, 2026).

**Out of scope:** OpenAI, Ollama, and Codex adapters; Failure Analysis and Targeted Reruns; Checkpoint and Resume — not planned for this expansion. Previously shipped capabilities listed below remain historical context.

**Already shipped:** Schema 2 and schema-1 read compatibility; task/attempt identities and fingerprints; named profiles; benchmark registry and five built-in suites (`code-edge`, `constraint-plan`, `knowledge-cal`, `multi-file`, `repo-issue`); pack validation and `benchmarks` commands; presets (`quick-check`, `coding-quality`, `full-evaluation`); Wilson/bootstrap statistics and exploratory repeats; schedule, writer lock, `resume`/`rerun`; failure categories and evidence artifacts; comparison eligibility (`compare_checked`); partial `setup` CLI/TUI.

## 1. Guided Benchmark Setup

**Goal:** Help users configure a useful run without learning every command-line option.

### Remaining

- [ ] Full keyboard-accessible setup flow for targets, benchmarks, repeat counts, and execution budgets (not just a read-only plan screen).
- [ ] Save the configuration as a named profile from setup and show an equivalent CLI invocation.
- [ ] Estimate runtime ranges from comparable saved runs in setup; keep `unknown` when history is insufficient.
- [ ] Support back navigation and cancellation without starting a run or losing entered settings.
- [ ] Pilot tests for setup/back navigation, invalid configurations, and small terminals.

**Acceptance criteria:** The same choices produce equivalent execution plans in the CLI and TUI; invalid combinations are caught before execution; the flow works with keyboard navigation, small terminals, and no-color settings.

## 2. Custom Benchmark Packs

**Goal:** Let users evaluate models against their own repeatable tasks.

### Remaining

- [ ] Run a validated local pack end-to-end and export task-level results.
- [ ] Pack authoring instructions (format, evaluators, fixtures, isolation requirements).

**Acceptance criteria:** A valid local pack runs and exports task-level results; malformed or unsafe packs fail clearly; altered fixtures cannot silently reuse a previous benchmark identity.

## 3. Quality–Speed Comparison Dashboard

**Goal:** Make it easy to compare correctness, responsiveness, and reliability across saved runs.

### Remaining

- [ ] TUI filters for target, benchmark, task set, and configuration when comparing saved runs.
- [ ] Success-versus-task-duration and success-versus-throughput views in the TUI, each with an accessible table.
- [ ] Display confidence estimates, sample counts, incomplete coverage, and unavailable metrics in comparison views.
- [ ] Export computed comparison view data through JSON, CSV, and Markdown.

**Acceptance criteria:** Incompatible runs never receive an automatic timing verdict; tables and plots agree; calculations live outside the renderers; comparison works without an inference server.

## 4. Reproducible Run Profiles

**Goal:** Save reusable configurations and enough provenance to explain differences between runs.

### Remaining

- [ ] User documentation for profiles, fingerprints, and comparison eligibility.
- [ ] Acceptance tests for profile precedence and secret exclusion beyond the core loader.

**Acceptance criteria:** Loading a profile reproduces its resolved configuration subject to explicit overrides; old results still open/export; secrets do not appear in persisted output.

## 5. Reliability and Confidence Reporting

**Goal:** Help users distinguish repeatable differences from small-sample noise.

### Remaining

- [ ] Expose computed statistics consistently in the TUI (plain output and stored `statistics` on runs are started).
- [ ] Deterministic tests for adaptive stop-at-budget, paired bootstrap, and low-sample suppression edge cases.
- [ ] Metrics/scoring documentation for confidence labels and adaptive-run regression policy.

**Acceptance criteria:** Extra repeats cannot erase earlier failures; reports disclose denominators and confidence methods; renderers perform no statistical calculations.

## 6. Benchmark Coverage and Results Dashboard

**Goal:** Show where each model performs well and which benchmarks still lack enough results to evaluate.

### Remaining

- [ ] Model-by-benchmark results matrix in the TUI, showing success rate, completed sample count, and coverage against the selected run's planned samples.
- [ ] Filters for saved run, model, benchmark category, and configuration; keep incompatible task sets and configurations separate.
- [ ] Keyboard-accessible cell details showing task-level scores, sample counts, and completed / failed / skipped / cancelled outcomes without requiring transcript inspection.
- [ ] Distinct labels for unrun tasks, incomplete coverage, and unavailable scores; provide a plain table equivalent and avoid relying on color alone.
- [ ] Export the displayed results matrix and coverage data through JSON, CSV, and Markdown using shared computed view data.
- [ ] Offline fixture and Pilot tests for mixed outcomes, empty history, filtering, keyboard navigation, and small terminals.

**Acceptance criteria:** Users can identify model strengths and coverage gaps from saved results without an inference server; missing results never appear as zero scores; denominators and outcome labels are explicit; matrix, details, and exports agree; calculations live outside renderers.
