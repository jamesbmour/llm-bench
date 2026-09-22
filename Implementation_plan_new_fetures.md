# Implementation Plan — 8 New Features

**Status:** Planned. This document covers only the eight feature additions; it does not redefine the existing app or specify new benchmark suites.

**Suggested implementation order:** 4 → 2 → 7 → 6 → 5 → 1 → 3 → 8. Deliver each feature with its focused tests and user documentation before proceeding to dependent work.

## 1. Guided Benchmark Setup

**Goal:** Help users configure a useful run without learning every command-line option.

**Depends on:** Custom Benchmark Packs and Reproducible Run Profiles.

### Implementation

- [ ] Add a keyboard-accessible setup flow for selecting targets, benchmarks, repeat counts, and execution budgets.
- [ ] Add versioned Quick Check, Coding Quality, and Full Evaluation presets using the benchmarks available in the selected installation.
- [ ] Show task counts, required capabilities, unsupported selections, and the resolved settings before starting.
- [ ] Estimate runtime ranges from comparable saved runs; display “unknown” when there is insufficient history.
- [ ] Allow users to save the configuration as a named profile and display an equivalent CLI invocation.
- [ ] Support back navigation and cancellation without starting a run or losing entered settings.

**Implementation areas:** Configuration resolution, CLI options, TUI setup screens, and the existing historical runtime estimator.

**Acceptance criteria:** The same choices produce equivalent execution plans in the CLI and TUI; invalid combinations are caught before execution; the flow works with keyboard navigation, small terminals, and no-color settings.

## 2. Custom Benchmark Packs

**Goal:** Let users evaluate models against their own repeatable tasks.

**Depends on:** Reproducible Run Profiles and versioned result identities from feature 4.

### Implementation

- [ ] Define a local `pack.toml` manifest containing pack/version identifiers, task IDs, prompts, fixtures, tool permissions, budgets, and evaluator configuration.
- [ ] Add a benchmark registry with declared capabilities instead of special cases based on scenario names.
- [ ] Add commands to list, inspect, validate, and explicitly select local packs.
- [ ] Initially support approved evaluator types; do not execute arbitrary manifest imports, installation hooks, or shell strings.
- [ ] Validate fixture hashes, unique IDs, file sizes, archive paths, symlinks, and writable paths before execution.
- [ ] Give each task a fresh workspace and preserve its pack version and content digest in results.
- [ ] Require isolated execution for custom executable tasks, with bounded resources and protected evaluator data; fail preflight if that execution policy is unavailable.
- [ ] Include a minimal example pack and authoring instructions.

**Implementation areas:** New pack loader and registry, scenario construction, execution-policy checks, selection, and task-level storage.

**Acceptance criteria:** A valid local pack runs and exports task-level results; malformed or unsafe packs fail clearly; altered fixtures cannot silently reuse a previous benchmark identity; existing built-in scenarios retain their behavior.

## 3. Quality–Speed Comparison Dashboard

**Goal:** Make it easy to compare correctness, responsiveness, and reliability across saved runs.

**Depends on:** Reproducible Run Profiles and Reliability and Confidence Reporting.

### Implementation

- [ ] Add offline selection of multiple saved runs, with filters for target, benchmark, task set, and configuration.
- [ ] Compute comparison eligibility before rendering, checking task versions, budgets, execution mode, and measurement provenance.
- [ ] Add success-versus-task-duration and success-versus-throughput views, each with an equivalent accessible table.
- [ ] Display confidence estimates, sample counts, incomplete coverage, failures, and unavailable metrics.
- [ ] Separate model-only and whole-agent results; distinguish cross-model exploration from same-model regression checks.
- [ ] Allow quality-only comparisons when timing conditions differ, with a clear explanation of timing incompatibility.
- [ ] Export the computed comparison data through the existing JSON, CSV, and Markdown formats.

**Implementation areas:** Pure comparison/view-model logic, saved-run loading, results screens, plain rendering, and exports.

**Acceptance criteria:** Incompatible runs never receive an automatic timing verdict; failed targets remain visible; tables and plots agree; calculations live outside the renderers; comparison works without an inference server.

## 4. Reproducible Run Profiles

**Goal:** Save reusable configurations and enough provenance to explain differences between runs.

**Depends on:** No other new feature; implement this foundation first.

### Implementation

- [ ] Add named profiles with list, save-from-run, inspect, and select operations.
- [ ] Resolve settings in this order: CLI > environment > selected profile > project configuration > user configuration > defaults; preserve existing behavior when no profile is selected.
- [ ] Record requested and observed inference settings separately, including model revision, quantization, context/output limits, temperature, and seed when available.
- [ ] Capture hardware, operating system, inference server, app/harness version, benchmark versions, and execution policy; leave unavailable values explicitly unknown.
- [ ] Introduce stable task, execution-target, repeat, and attempt identities with canonical non-secret configuration fingerprints.
- [ ] Extend result storage through a versioned migration; older runs remain readable without rewriting their original files or inventing missing metadata.
- [ ] Use task/attempt-aware transcript and artifact names to prevent collisions.
- [ ] Exclude credentials and secrets from profiles, fingerprints, evidence, and exports.

**Implementation areas:** New profile loader, configuration resolution, result schema, migration, transcript naming, and comparison metadata.

**Acceptance criteria:** Loading a profile reproduces its resolved configuration subject to explicit overrides; old results still open/export; unknown metadata stays unknown; mismatched task/configuration fingerprints are detected; secrets do not appear in persisted output.

## 5. Reliability and Confidence Reporting

**Goal:** Help users distinguish repeatable differences from small-sample noise.

**Depends on:** Versioned task results and profiles from features 2 and 4.

### Implementation

- [ ] Report independent task counts, repeat counts, timing variability, scored coverage, errors, skips, and cancellations.
- [ ] Add 95% Wilson intervals for repeated binary outcomes on a fixed task, labeled as repeat reliability.
- [ ] Compute suite uncertainty by resampling task-level clusters; resample matched tasks together for paired comparisons.
- [ ] Suppress suite confidence claims for insufficient task counts and document that repeated attempts are not independent tasks.
- [ ] Keep fixed-repeat runs as the default; add optional exploratory repeats with declared precision targets, repeat limits, and time/token caps.
- [ ] Persist the repeat policy and stopping decisions; label adaptively stopped intervals as descriptive and disable automatic regression verdicts for those runs.
- [ ] Expose all computed statistics consistently in the TUI, plain output, and exports.

**Implementation areas:** Pure statistics functions, result aggregation, runner repeat scheduling, and presentation fields.

**Acceptance criteria:** Deterministic tests cover all-pass/all-fail outcomes, sparse results, unequal repeats, paired comparisons, and budget stops; extra repeats cannot erase earlier failures; reports disclose denominators and confidence methods.

## 6. Failure Analysis and Targeted Reruns

**Goal:** Explain unsuccessful attempts and rerun only the samples needed for investigation.

**Depends on:** Stable identities from feature 4 and durable attempt handling from feature 7.

### Implementation

- [ ] Add structured failure categories for incorrect answers, invalid output, budget exhaustion, tool-policy violations, provider errors, and evaluator errors.
- [ ] Capture named assertions, bounded test logs, tool-call errors, code diffs, and truncation indicators before workspace cleanup.
- [ ] Store redacted evidence as artifacts referenced by the sample result.
- [ ] Extend transcript inspection with failure filters and lazily loaded evidence.
- [ ] Add CLI and TUI actions to rerun an explicitly selected sample or failed-sample set.
- [ ] Create linked child runs for reruns, preserving original scores and attempt history.
- [ ] Mark reruns informed by revealed hidden tests as diagnostic and exclude them from untouched evaluation comparisons.

**Implementation areas:** Structured score/evidence records, evaluator output, artifact storage, runner sample selection, and transcript screens.

**Acceptance criteria:** Evidence remains available after workspace cleanup; users can identify and rerun a particular failure; reruns never overwrite original results; incorrect solutions remain distinguishable from infrastructure failures.

## 7. Checkpoint and Resume

**Goal:** Continue interrupted evaluations without repeating finished work or losing evidence.

**Depends on:** Versioned task identities and result storage from features 2 and 4.

### Implementation

- [ ] Persist the resolved task/repeat schedule before execution and checkpoint every terminal attempt.
- [ ] Commit evidence before atomically publishing the manifest that references it; recover safely from partial writes.
- [ ] Add an exclusive run lock to prevent concurrent writers.
- [ ] Add an explicit resume path that validates target, configuration, and fixture fingerprints before execution.
- [ ] Skip completed attempts; restart interrupted tasks in fresh workspaces with new attempt IDs while retaining partial transcripts.
- [ ] Preserve completed failures and deliberate skips; retry them only through explicit targeted reruns.
- [ ] Reacquire and warm models for the resumed session, recording new lifecycle timing and respecting current instance ownership.
- [ ] Reject legacy or incomplete records that lack enough scheduling information for safe resume, while keeping them viewable.

**Implementation areas:** Runner scheduling, run/attempt state, atomic storage, locking, lifecycle handling, and CLI/TUI resume actions.

**Acceptance criteria:** Interruptions around commit boundaries cause no lost committed evidence or duplicate completed samples; concurrent resume is rejected; changed fingerprints block execution; resumed runs preserve both prior results and new session provenance.

## 8. Model and Agent Adapters

**Goal:** Evaluate additional inference engines and coding agents through explicit, comparable execution contracts.

**Depends on:** Profiles, packs, durable execution, and comparison rules from features 2–7.

### Implementation

- [ ] Add a generic OpenAI-compatible inference adapter, followed by an Ollama adapter, using the provider contract.
- [ ] Publish capabilities for discovery, tool use, streaming, token usage, loading, and unloading; keep unsupported operations explicit.
- [ ] Define a separate external-agent execution interface for capability inspection, task execution, events/artifacts, cancellation, and cleanup.
- [ ] Implement Codex first through a documented noninteractive interface, verifying installed versions and current official documentation before selecting command flags or event schemas.
- [ ] Record agent version, model identity when available, instructions/configuration, enabled tools, and execution policy.
- [ ] Keep scheduling, independent evaluation, persistence, and final scoring under the runner's control.
- [ ] Require enforceable tool restrictions for tool-free tasks; isolate candidate tools and expose only the inference connection required by the selected execution policy.
- [ ] Report whole-task duration separately; show TTFT and throughput as unavailable when the agent does not expose the necessary timing events.
- [ ] Add fake HTTP/process contract tests and separately record a live smoke run for each adapter before advertising it as validated.

**Implementation areas:** Provider adapters, a new agent interface and Codex adapter, runner dispatch, capability preflight, and target selection.

**Acceptance criteria:** Supported targets run through the same task/evaluator contracts; cancellation preserves evidence and cleans up owned resources; missing measurements are not fabricated; reports distinguish model-plus-harness performance from whole-agent performance.
