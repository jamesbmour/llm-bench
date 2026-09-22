# Implementation Plan — Remaining New Features

## 1. Quality–Speed Comparison Dashboard

**Goal:** Make it easy to compare correctness, responsiveness, and reliability across saved runs.

### Completed

- [x] TUI filters for target, benchmark, task set, and configuration when comparing saved runs.
- [x] Success-versus-task-duration and success-versus-throughput views in the TUI, each with an accessible table.
- [x] Display confidence estimates, sample counts, incomplete coverage, and unavailable metrics in comparison views.
- [x] Export computed comparison view data through JSON, CSV, and Markdown.

**Acceptance criteria:** Incompatible runs never receive an automatic timing verdict; tables and plots agree; calculations live outside the renderers; comparison works without an inference server.

## 2. Benchmark Coverage and Results Dashboard

**Goal:** Show where each model performs well and which benchmarks still lack enough results to evaluate.

### Completed

- [x] Model-by-benchmark results matrix in the TUI, showing success rate, completed sample count, and coverage against the selected run's planned samples.
- [x] Filters for saved run, model, benchmark category, and configuration; keep incompatible task sets and configurations separate.
- [x] Keyboard-accessible cell details showing task-level scores, sample counts, and completed / failed / skipped / cancelled outcomes without requiring transcript inspection.
- [x] Distinct labels for unrun tasks, incomplete coverage, and unavailable scores; provide a plain table equivalent and avoid relying on color alone.
- [x] Export the displayed results matrix and coverage data through JSON, CSV, and Markdown using shared computed view data.
- [x] Offline fixture and Pilot tests for mixed outcomes, empty history, filtering, keyboard navigation, and small terminals.

**Acceptance criteria:** Users can identify model strengths and coverage gaps from saved results without an inference server; missing results never appear as zero scores; denominators and outcome labels are explicit; matrix, details, and exports agree; calculations live outside renderers.

## 3. Reliability and Confidence Reporting

**Goal:** Help users distinguish repeatable differences from small-sample noise.

### Remaining

- [ ] Expose computed statistics consistently in the TUI (plain output and stored `statistics` on runs are started).
- [ ] Deterministic tests for adaptive stop-at-budget, paired bootstrap, and low-sample suppression edge cases.
- [ ] Metrics/scoring documentation for confidence labels and adaptive-run regression policy.

**Acceptance criteria:** Extra repeats cannot erase earlier failures; reports disclose denominators and confidence methods; renderers perform no statistical calculations.
