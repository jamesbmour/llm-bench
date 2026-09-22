# llmsweep — Implementation Plan

The v1 foundation below remains the compatibility contract. The [feature and benchmark expansion](#feature-and-benchmark-expansion) defines the next implementation milestones, covering eight proposed features and five additional benchmarks.

**Expansion status:** planned; no expansion feature is marked implemented by this document. Updated September 22, 2026. Milestones 1–5 describe the existing v1 foundation; milestones 6–13 describe the new work.

## v1 foundation — LM Studio

## Summary

Build a new Python 3.11+ CLI and Textual application for LM Studio on macOS and Linux. Support all three benchmarks, repeat measurements, saved runs, transcripts, exports, and baseline comparisons.

Use the existing `lmstudio_agent_bench.py` only as LM Studio integration guidance. The brief defines scoring; the new implementation will have independent architecture and tests.

Default to all three scenarios, serial execution, and one load/warmup per model followed by all repeats. Defer Ollama, generic OpenAI, OpenRouter, billing, and provider switching.

## Architecture and measurement rules

- Use the requested `src/llmsweep` layout, hatchling packaging, typed dataclasses, and `from __future__ import annotations`. Keep the reference script unchanged and outside the installed package and new-code validation targets.
- Define explicit interfaces for `Provider`, `Scenario`, `RunSession`, normalized stream events, and results. Model identity remains `(provider, id)` throughout selection, storage, and comparisons.
- The runner owns execution, cancellation, measurements, and persistence. Plain and TUI renderers consume the same events and results; neither calculates scores or metrics.
- Store turn-level observations, repeat/scenario samples, scenario rollups, and model summaries. Separate completed, failed, skipped, and cancelled outcomes; retain failed models in reports.
- TTFT starts when the request is dispatched and ends at the first content, reasoning, or tool delta. Ignore role-only headers and usage packets.
- LM Studio throughput uses generated tokens divided by the streamed generation window, from first output delta to last output delta. Label this as client-observed throughput. Exclude load, warmup, tool execution, and checker time; zero-duration samples produce `n/a`.
- Prefer completion-token usage, including reasoning already counted within it. Otherwise use a documented, chunk-independent estimate of UTF-8 output bytes divided by four, rounded up. Preserve `token_source`; record output tokens separately from API `total_tokens`.
- A scenario sample pools tokens and generation durations across its turns. Aggregate repeats with mean, median, and nearest-rank p95. Model throughput is explicitly labeled **mean of scenario means**.
- Baselines match provider, model, scenario, and benchmark settings. Apply the configured 5% throughput/TTFT thresholds to scenario means. Differing token sources, incomplete samples, and contended parallel runs receive comparison warnings and no automatic performance verdict.

## Implementation sequence

Each milestone must pass its applicable tests, Ruff, and strict mypy before the next begins.

### 1. Pure logic

Implement `streams.py`, `models.py`, `selection.py`, and `metrics.py` without I/O.

- Incrementally decode SSE across arbitrary byte boundaries, including UTF-8 splits, comments, reasoning, indexed tool fragments, interleaved text/tools, and usage packets with empty `choices`.
- Raise typed parsing errors for malformed SSE data. An NDJSON fixture fed into this SSE decoder must yield zero deltas.
- Normalize LM Studio metadata without filtering by model name. Exclude `embedding`, `embeddings`, and `reranker`; retain an `llm` whose name contains “embed.”
- Prefer `params_string` for size; use the specified name parser only when metadata is unavailable. Unknown sizes sort last.
- Share selection resolution between CLI and Picker: exact ID first, indices/ranges second, unique substring last; preserve order and deduplicate.
- Establish pure aggregation, percentile, missing-value, and baseline rules with fixture-driven tests.

### 2. LM Studio provider and HTTP stubs

Implement an async `httpx` adapter and socket-free fake HTTP servers.

- Discover through v1; fall back to v0 only for an unsupported endpoint, not authentication, malformed responses, or transient failures.
- Use `/v1/chat/completions` with streaming and usage collection. Preserve tool argument strings until the complete call can be validated.
- Snapshot loaded instances. Load only when needed, capture the returned instance ID, and poll readiness every second within the load deadline. Prefer reported load time; otherwise measure through readiness. The API documents both instance identity and load duration. [LM Studio load API](https://lmstudio.ai/docs/developer/rest/load)
- Unload only positively identified instances created by this run, then verify their disappearance. Never substitute a model name for an unknown instance ID.
- Reconcile uncertain load/unload outcomes before retrying. Make cancellation and cleanup idempotent, with partial persistence and explicit cleanup failures.
- Separate DNS, connection, TLS, timeout, HTTP, malformed-response, stream, and model errors. Allow at most three attempts for eligible connection/5xx failures, with jitter; never restart a stream after output.
- Scope credentials to the configured origin, disable automatic redirects, and redact secrets before logging, exporting, or persisting.

Parameterize the contract suite over LM Studio v1 and v0 fixtures; keep assertions independent of adapter internals.

### 3. Scenarios, runner, results, and store

Make `llmsweep run --plain` complete a scored run against stubs.

- Implement the weather tools, temperature regex, and required-tool scoring exactly as specified.
- Implement the agent-code workspace, read limits, grep limits, guarded writes, required `write_file` call, and independent final test rerun.
- Implement code extraction and the exact Fibonacci checker, including the 15-second timeout. Valid unfenced Python may pass; unfenced prose must fail.
- Give every repeat fresh scenario state. Execute generated Python in subprocesses with sanitized environments, bounded output, timeouts, and process-group cleanup.
- Persist schema-versioned results and transcripts atomically after completed samples and during cancellation. Use provider-prefixed, collision-resistant filenames.
- Maintain an atomic store index and an explicit migration dispatcher. Reject unknown/future schemas with actionable errors.
- Preserve partial results and distinguish incorrect answers from execution errors. Scoring failure alone does not produce the “model errored” exit code.

### 4. Plain renderer and complete CLI

Implement `run`, `list`, `show`, `export`, `doctor`, and `providers`, plus the top-level aliases.

- Preserve applicable flag names. `providers` lists LM Studio; selecting an unimplemented provider or requesting cost sorting exits 2.
- Implement CLI > environment > project TOML > user TOML precedence, explicit config-file selection, unknown-key validation, and environment-variable references for secrets.
- Decide plain versus TUI before importing/constructing the App. Pipes, non-TTY sessions, `TERM=dumb`, and CI always use ANSI-free plain output.
- Make `show` and `export` fully offline. JSON exports use the canonical run representation; CSV and Markdown expose missing values and token-source warnings.
- Keep the defined exit codes: setup/configuration errors 2, non-retryable authentication failures 4, model errors 1, regression-only failures 3, otherwise 0.
- `doctor` checks configuration, discovery, authentication, and store accessibility without loading models or sending benchmark requests.

### 5. Textual TUI and release deliverables

Implement Picker, Live, Results, and Transcript screens over the validated runner.

- Use external `theme.tcss`, DataTables, RichLog, Sparkline, ProgressBar, Footer, built-in HelpPanel, and the enabled command palette.
- Own async `@work` workers on the App, with `exit_on_error=False` and `exclusive=False`. Track workers by model identity so rerunning one cancels only that model.
- Handle `Worker.StateChanged` explicitly: display `worker.error`, accept `ModelResult` on success, and coordinate runner cleanup/persistence on cancellation. These behaviors use Textual’s documented worker lifecycle. [Textual workers](https://textual.textualize.io/guide/workers/)
- Buffer stream output and flush every 75 ms. Resize reflows the view without replacing or losing buffered data.
- Implement the specified selection, search, transcript, sorting, diff, export, and cancellation keys. `ctrl+c` explains `ctrl+q`; quitting waits for cleanup.
- Show ETA only from comparable stored runs. Honor no-color preferences and pair regression arrows with words.
- Finish README, flag documentation, CHANGELOG, design rationale and assumptions, and an actual `export_screenshot()` image. Add macOS/Ubuntu CI for Python 3.11 and the current stable Python release.

## Validation

All tests run without network access; an autouse guard rejects accidental socket connections.

- Stream fixtures cover fragmentation, usage-only endings, mixed reasoning/text/tools, malformed arguments, unknown tools, mid-stream disconnection, and 500-then-success.
- Provider tests cover v1/v0 normalization, readiness polling, reported versus measured load time, secret redaction, and preservation of preloaded instances.
- Scoring tests include every positive and negative case in the brief, plus traversal, symlink escape, read-only writes, timeout, and cancellation cleanup.
- Selection and baseline tests include numeric IDs, colon-containing IDs, ambiguity, invalid ranges, and mismatched provider identities.
- Deterministic clock/ID injection proves byte-identical persisted output from plain and TUI paths for the same runner observations.
- Pilot tests assert widget state, worker isolation, responsiveness during delayed chunks, cancellation cleanup, and lossless resizing. Include a synthetic stream exceeding 1,000 deltas/second.
- Release gates: offline `pytest`, `ruff check`, `mypy --strict`, package build/install, and CLI smoke tests.

## Assumptions and resolved ambiguities

- **Confirmed:** all three scenarios run by default; each model loads and warms once for all repeats.
- Defaults: one repeat, 1,024 output tokens per turn, 300-second request inactivity timeout, and a 600-second load deadline. Scenario turn limits remain 6/10/1 unless explicitly overridden where applicable.
- Without model selection, an interactive run opens Picker; plain mode requires `--models` or `--all`.
- `--task` customizes weather only and disables its score. It is rejected when explicitly combined with other scenarios.
- Known lack of tool support skips tool scenarios. Unknown support is attempted with a warning; `--require-tool-use` selects only explicitly advertised support.
- `--keep-loaded` and `--no-unload` are aliases. Otherwise cleanup unloads only run-owned instances. Parallel execution requires all selected instances to be already loaded.
- Preloaded models and v0 sessions have `load_s: null` with an explanatory status. v0 may use JIT loading; unavailable load timing and unload control are reported honestly.
- The Picker’s embeddings filter can only narrow eligible chat models; embeddings never become selectable. Provider-switch controls are deferred.
- Tool path guards and tempdirs provide workflow confinement, not an OS security sandbox. Explicit export destinations are permitted writes.
- Byte identity applies to identical captured observations, timestamps, and run IDs—not separate live experiments.
- Existing reference-script result formats are not treated as historical llmsweep schemas. Multi-provider acceptance cases and billing controls are deferred with their providers.

## Feature and benchmark expansion

### Product objective and scope

Help users answer three questions: which model or coding agent succeeds at their work, how quickly it succeeds, and whether the difference is repeatable. Preserve the local-first CLI/TUI workflow and existing measurement definitions.

All eight features and five benchmarks proposed in the product discussion are in scope. New benchmarks are opt-in; an existing invocation continues to select `weather`, `agent-code`, and `codegen`. Existing flags, exit codes, saved runs, and score meanings remain compatible. New command examples below are proposed interfaces, not currently available commands.

“Codex” means an external coding-agent execution target when selected through the agent adapter. Running a model with LLMSweep's own tools measures that model plus LLMSweep's harness; running Codex measures the model plus Codex's configuration and tools. Reports must identify the execution target and never present these as interchangeable experiments.

Non-goals for this expansion: a hosted leaderboard, billing or price estimates, arbitrary remote plugin execution, a browser dashboard, automatic publication, and importing the entire SWE-bench dataset. Public benchmark integrations can follow after the bundled suites are validated.

### Current implementation and required changes

The current source, rather than older milestone notes, establishes the starting point:

| Current seam | Expansion work |
| --- | --- |
| `scenarios/__init__.py:create_scenario` selects three built-in scenarios | Introduce a metadata registry and versioned task definitions; preserve existing names. |
| `Scenario.score` returns a success flag and text | Add structured scoring evidence with a compatibility wrapper for existing scenarios. |
| `RunSession.run_model` treats every scenario except `codegen` as tool-dependent | Use declared capabilities so reasoning and knowledge tests work on models without tools. |
| `RunSession.prepare` replaces model results; `run_all` starts a new schedule | Add an explicit execution plan and a separate resume path that preserves completed samples. |
| `SampleResult` identifies a scenario and repeat | Add task, suite, attempt, and execution-target identities before supporting multiple tasks per suite. |
| `RunResult.schema_version` and `load_run` currently support schema 1 | Implement an actual migration dispatcher and a backward-compatible schema 2 reader/writer. |
| Transcript filenames use provider/model/scenario/repeat | Include task and attempt identities to prevent overwrites. |
| `compare_runs` checks selected settings and token sources | Add task-set, environment, harness, and inference-setting compatibility checks. |
| TUI has results, transcripts, baseline diff, exports, and model reruns | Extend these views with confidence, evidence, sample reruns, and cross-run comparisons. |
| `RunStore.estimate` uses matching stored settings | Reuse it behind a richer comparable-history estimator; retain unknown estimates as `None`. |

Paths above are relative to `src/llmsweep/`. Existing uncommitted application work is outside this planning change. At implementation kickoff, refresh the baseline checks and reconcile current source before modifying these seams.

### Feature delivery map

| ID | Feature | Deliverable and primary benefit | Milestones |
| --- | --- | --- | --- |
| F1 | Guided Benchmark Setup | Keyboard-accessible setup with Quick Check, Coding Quality, and Full Evaluation presets, capability checks, and historical runtime ranges; lowers setup effort. | 7, 12 |
| F2 | Custom Benchmark Packs | Local, versioned manifests containing tasks, fixtures, budgets, tool permissions, and approved evaluator types; measures relevant work. | 6, 7 |
| F3 | Quality–Speed Comparison Dashboard | Comparable-run filtering, success versus latency/throughput views, and accessible tables; makes tradeoffs visible. | 6, 10, 12 |
| F4 | Reproducible Run Profiles | Named configurations plus actual environment, model, harness, and task fingerprints; makes reruns auditable. | 6, 7 |
| F5 | Reliability and Confidence Reporting | Task-aware confidence estimates, variability, sample counts, and bounded exploratory repeats; reduces overinterpretation. | 10 |
| F6 | Failure Analysis and Targeted Reruns | Structured assertions, bounded logs, code diffs, failure categories, and linked sample reruns; accelerates diagnosis. | 6, 9, 11, 12 |
| F7 | Checkpoint and Resume | Durable task scheduling, interruption recovery, exclusive writers, and preserved attempts; avoids repeating finished work. | 6, 11 |
| F8 | Model and Agent Adapters | Additional inference providers and a distinct external-agent interface, beginning with Codex; expands evaluation targets. | 6, 13 |

### Architecture and data contracts

Keep the pure core synchronous and free of I/O. Keep `errors` and `security` as leaves. Scoring math and comparison eligibility belong in domain modules; execution, cancellation, and persistence belong in the runner; renderers consume computed results.

Proposed new modules, introduced only when their milestone needs them:

| Module | Responsibility |
| --- | --- |
| `benchmarks/types.py`, `benchmarks/registry.py` | Immutable suite/task definitions, capability requirements, stable IDs, and registry lookup. |
| `packs.py`, `profiles.py` | Manifest/profile loading, validation, resolution, and fingerprints; all filesystem access stays here. |
| `evaluators/` | Deterministic evaluators and structured evidence; pure logic separate from checker execution. |
| `execution/` | Workspace preparation, bounded processes, isolated candidate execution, and tool permissions. |
| `statistics.py`, `comparison.py` | Pure uncertainty calculations, denominators, matching, and dashboard view models. |
| `agents/base.py`, `agents/codex.py` | External-agent execution contract and Codex process adapter. |

Extend `results.py`, `store.py`, `runner.py`, `config.py`, `selection.py`, `plain.py`, and the existing TUI rather than introducing another run store or measurement pipeline. `benchmarks/types.py` must not import providers, agents, the runner, or renderers. Keep construction/wiring at the application boundary to avoid circular imports.

#### Identity and schema 2

- Retain `(provider, id)` as model identity. Add `target_kind` (`model` or `agent`) and an execution configuration fingerprint; never replace model identity with an agent display name.
- Define `TaskRef` using pack ID, pack version, suite ID, task ID, and content digest. Persist the selected task manifest and its order with the run.
- Define a sample key using execution target, `TaskRef`, repeat index, and attempt ID. Preserve seed, parent attempt/run, status, start/end observations, budget consumption, and interruption reason.
- Add `ScoreResult`: evaluator ID/version, `success: bool | None`, named metric values with units/direction, failure code, and bounded evidence/artifact references. Keep infrastructure errors distinct from incorrect answers and policy violations.
- Add `EnvironmentSnapshot`: OS/architecture, CPU/GPU and memory when available, inference server version, model revision/quantization when available, LLMSweep/harness version, and execution policy. Record requested and observed settings separately, including temperature, seed, context limit, and output limit. Unknown or unsupported values remain `None` with provenance.
- Schema 1 loads through a pure migration function with explicit legacy identities and unknown metadata; reading must not rewrite the original file. Future schemas and malformed records fail with typed errors. Migration does not fabricate reproducibility metadata or make old runs resumable without a complete schedule.
- Write bounded, redacted artifacts first and atomically commit a run manifest referencing them; a manifest never references partially written evidence. Use content hashes, safe filenames, and explicit retention limits. Existing JSON/CSV/Markdown exports remain available; publish new columns and compatibility notes.

#### Measurement and comparison policy

- Preserve TTFT, generation-window throughput, token-source accounting, existing rollups, and the 5% regression thresholds from v1.
- Add end-to-end task duration, tool duration, checker duration, tool-call count, and completion rate as separate metrics. They must not enter generation-window throughput.
- Within new suites, compute each task's mean over repeats, then the suite mean over tasks. Preserve the model's mean-of-scenario-means throughput contract; comparisons require the same selected suites and task sets.
- Keep fixed-task repeat variability separate from generalization across tasks. More repeats of one task do not create more independent tasks.
- Report correctness among scored attempts alongside scored count, attempted count, infrastructure-error count, skipped count, cancelled count, and planned coverage. Show operational completion separately so failed runs cannot disappear from a “best model” ranking.
- Baseline regressions require the same model identity and compatible configuration. Cross-model exploration may deliberately vary model/quantization; it still requires matched task sets, budgets, harness, and measurement provenance. Label which dimensions differ.
- Timing compatibility requires matching hardware/server context, warmup policy, inference settings, execution mode, and token source. Quality-only comparisons may remain available when hardware differs. Unknown critical metadata yields “not comparable” for automated timing verdicts.
- External agents without dispatch and output-delta timestamps receive `None` for TTFT/throughput. Report observed whole-task duration and explicitly sourced usage instead of deriving token speed from process stdout or total elapsed time.

### Benchmark specifications

Each suite has development examples and a held-out evaluation partition, immutable versioned fixtures, a deterministic oracle, difficulty tags, documented budgets, and fresh state per task/repeat. Freeze the evaluation task list before running a comparison. Seeded ordering and generated fixtures are persisted; model determinism is not assumed.

The initial task counts below are delivery targets for useful local suites, not claims of statistical representativeness. Default limits are initial guardrails to calibrate before release; changes create a new profile or benchmark version.

#### B1 — Edge-Case Code Generation (`code-edge`, Coding)

- **Measures:** First-attempt functional correctness and handling of boundary conditions across function-level programming problems.
- **Initial corpus:** 20 Python tasks spanning collections, parsing, Unicode, numeric boundaries, and algorithms; include empty, invalid, and large inputs with behavior specified in the prompt.
- **Execution:** One generation turn, no tools or corrective feedback, 2,048 output tokens, 120-second task budget, and at most 15 seconds per checker invocation. Performance limits guard execution; this release does not claim an algorithm-efficiency score.
- **Scoring:** A task passes only when all independent assertions pass; report first-attempt task pass rate and diagnostic test-group results. Reference solutions and known incorrect variants validate the checker.
- **Acceptance:** Correct reference implementations pass; off-by-one, Unicode, type-contract, and complexity-related failure fixtures are caught; truncated output and timeouts fail predictably. Test details remain outside the candidate's accessible workspace.

#### B2 — Multi-File Feature Implementation (`multi-file`, Agentic coding)

- **Measures:** Repository navigation, coordinated changes, adherence to a feature contract, and preservation of existing behavior.
- **Initial corpus:** Six self-contained Python mini-repositories with roughly 5–15 source files each; tasks span a CLI option, parser capability, validation rule, configuration propagation, serialization change, and module integration.
- **Execution:** Read/search/write/run-public-tests tools; 20 turns, 4,096 output tokens per turn, 32,768 total completion-token budget, and a 300-second task budget. Checkers have separate bounded execution deadlines.
- **Scoring:** All hidden acceptance tests and existing regression tests pass, and protected files remain unchanged. Grade observable behavior, not whether the patch matches the reference implementation. Record changed files and tool errors diagnostically.
- **Acceptance:** Test partial implementations, correct alternatives, regression-inducing changes, attempted test edits, traversal/symlink escapes, stalled subprocesses, and cancellation. Every repeat starts from the original repository digest.

#### B3 — Repository Issue Resolution (`repo-issue`, Agentic coding)

- **Measures:** Ability to reproduce a defect, diagnose its cause, repair it, and preserve previously working behavior.
- **Initial corpus:** Eight pinned repository snapshots with issue descriptions, public reproduction commands, failing defect tests, and passing regression tests. Start with redistributable project-authored fixtures; record license and provenance for any later imported case.
- **Execution:** Same tools and budgets as `multi-file`; dependencies must be preinstalled from pinned artifacts. No dependency downloads during scoring. Track reproduction attempts separately from final patch success.
- **Scoring:** All designated fail-to-pass tests must pass and all pass-to-pass tests must remain passing. A model's claim that tests passed is not evidence. No matching of patches against a single reference diff.
- **Acceptance:** Verify the defective baseline fails the intended tests, the reference fix passes, and no-op, test-deletion, hard-coded, and regression-inducing patches fail. Describe results as LLMSweep repository-issue scores, not SWE-bench scores.

#### B4 — Constraint-Based Planning (`constraint-plan`, Reasoning)

- **Measures:** Correct reasoning about schedules, dependencies, capacities, and optimization under explicit constraints.
- **Initial corpus:** 30 small problems with seeded variants, split across scheduling, dependency ordering, and resource allocation; include feasible, infeasible, and multiple-valid-solution cases.
- **Execution:** One turn, no tools, 2,048 output tokens, 120-second task budget; request a compact structured final answer. Do not require or grade private reasoning traces.
- **Scoring:** Validate the answer schema, every constraint, and any claimed objective value using a bounded independent solver. Report feasibility accuracy and optimality separately; infeasibility claims must agree with the oracle.
- **Acceptance:** Accept alternate valid optima, reject plausible but invalid schedules and false infeasibility claims, and verify oracle results against exhaustive enumeration on small fixtures. Parse failures receive structured feedback, not grader crashes.

#### B5 — Knowledge Accuracy and Calibration (`knowledge-cal`, General knowledge)

- **Measures:** Factual accuracy, confidence calibration, and appropriate abstention without browsing or retrieval tools.
- **Initial corpus:** 100 curated questions balanced across science, history, geography, and technology; include 20 explicitly unanswerable or underspecified items. Store authoritative provenance, accepted answers, explanation, review date, and license for each item. Avoid rapidly changing facts.
- **Execution:** One independent question per sample, one turn, 512 output tokens, 60-second task budget. Request `{answer, confidence, abstain}` with confidence in `[0, 1]`; use multiple-choice or narrowly normalized short answers.
- **Scoring:** Report answerable-item accuracy, answered-item accuracy, coverage, and correct abstention on unanswerable items. Compute Brier score for the stated probability that a submitted answer is correct over answered items; always show its coverage and count. Abstention cannot improve the main answerable-item accuracy score. Invalid outputs count as unsuccessful responses and have no calibration value.
- **Acceptance:** Cover answer aliases, boundary confidence values, nonfinite/out-of-range values, confident errors, valid abstentions, and ambiguous questions. Have a separate content review remove ambiguity before freezing the evaluation set.

### Execution and evaluator isolation

Broader code tasks must not inherit a misleading sandbox claim from v1's temporary directories and process groups. Introduce an explicit execution policy before exposing custom executable tasks or external agents:

- Candidate processes run in disposable isolated environments with no host-home mounts, no inherited credentials, no network by default, bounded CPU/memory/processes/output/time, and only the task workspace writable. On macOS this may require a container VM; capability detection must report unavailable prerequisites.
- The trusted controller holds the answer key, hidden inputs, and scoring logic outside candidate access. For function tasks, send individual inputs to isolated candidate processes and compare outputs in the controller. For repository tasks, evaluate the patch in a fresh environment and independently validate structured test results; never trust a success marker printed by candidate code.
- Public development tests may be exposed; private grading details are accessible only after the scored attempt closes. A diagnostic rerun after revealing those details is marked as such and excluded from untouched held-out comparisons.
- Custom packs initially choose approved evaluator types and parameters. Do not dynamically import Python from manifests, interpolate shell commands, or automatically execute pack install hooks. Validate archive paths, symlinks, file counts, sizes, hashes, and allowed writes before materialization.
- For remote inference, a trusted controller may use credentials for the configured origin. Candidate tools remain network-disabled. An external agent's required inference connection must be narrowly allowed and logged separately from tool/browser access; if that separation cannot be enforced, report an unsupported execution policy.
- Preserve the existing v1 workflow-confinement mode for compatibility. New executable packs require the stronger policy and fail preflight with a setup error if it is unavailable; do not silently downgrade it.

### Implementation milestones

Complete each milestone's acceptance checks before starting dependent work. All items below start unchecked. Module names are proposed; preserve small interfaces rather than expanding every existing class at once.

#### 6. Versioned results, profiles, and comparison foundations

**Dependencies:** Existing v1 foundation. **Delivers:** F4 foundation; shared infrastructure for F2–F8.

- [ ] Add task/target/attempt IDs, structured scores, manifests, environment snapshots, and schema 2 migration.
- [ ] Separate requested settings from observed provider values; hash canonical non-secret configuration and fixture content.
- [ ] Introduce pure comparison eligibility rules and retain v1 metric semantics.
- [ ] Make transcript/artifact naming collision-resistant across task IDs, agent targets, retries, and repeats.
- [ ] Add named profile resolution: CLI > environment > selected profile > project config > user config > defaults; no selected profile preserves existing precedence. Explicitly reject profile/CLI combinations that make a run ambiguous.

**Acceptance:** Schema 1 fixtures still open and export offline without rewriting; unknown future schemas fail clearly; mismatched task digests block automatic comparisons; secrets are absent from snapshots, hashes' serialized inputs, logs, and exports. Identical captured runner observations still serialize identically through plain and TUI paths.

#### 7. Benchmark registry, pack validation, and execution policy

**Dependencies:** 6. **Delivers:** F2, reusable profiles for F4, preset metadata for F1.

- [ ] Replace scenario-name special cases with declared tool requirements, execution kind, supported languages, turn limits, and evaluator capabilities.
- [ ] Define `pack.toml` format with manifest schema version, pack version, suite/task IDs, category/difficulty, prompts, fixture hashes, permitted tools/writes, budgets, evaluator ID/version, and provenance/license metadata.
- [ ] Add local `benchmarks list`, `benchmarks inspect`, `benchmarks validate`, and profile list/save commands; packs must be explicitly selected and never auto-loaded from arbitrary repository content.
- [ ] Implement isolated execution and trusted evaluation boundaries above; add preflight capability reporting to `doctor` without sending benchmark requests.
- [ ] Wrap the three existing scenarios through the registry without changing their default selection or scoring contract.

**Acceptance:** Valid packs round-trip; duplicate IDs, unsupported evaluators, changed hashes, invalid budgets, traversal, and unsafe archives are rejected. Models without tools can run tool-free suites. Local pack validation and fixture checks require no inference server or network.

#### 8. Coding, reasoning, and knowledge suites

**Dependencies:** 6–7. **Delivers:** B1, B4, B5.

- [ ] Implement the three suite evaluators and initial corpora specified above.
- [ ] Persist structured per-task results, confidence values where applicable, selected partitions, and checker evidence.
- [ ] Add explicit task selection and seeded ordering; preserve task-level results rather than collapsing each suite into one prompt.
- [ ] Document each suite's scoring, limitations, output schema, and version policy.

**Acceptance:** Reference answers pass and deliberately incorrect answers fail for every evaluator family; all oracle checks run offline; corpus review and provenance are complete. Fake-provider end-to-end tests prove selection, capability handling, scoring, persistence, and export for all three suites.

#### 9. Repository benchmarks and failure evidence

**Dependencies:** 6–8. **Delivers:** B2, B3; F6 evidence foundation.

- [ ] Generalize workspace tools to bounded multi-file reads/searches/edits and allowlisted public test commands.
- [ ] Implement immutable fixture restoration and independent acceptance/regression checking.
- [ ] Capture redacted patch diffs, named test outcomes, tool-call failures, output truncation, and budget-exhaustion reasons before workspace cleanup.
- [ ] Distinguish incorrect solution, invalid output, task budget exhaustion, tool-policy violation, provider failure, and evaluator infrastructure failure.

**Acceptance:** Meet B2/B3 negative-case requirements; a changed or deleted checker cannot produce a pass; no candidate can access host secrets or evaluator answer keys; cancellation removes child processes and only run-owned resources. Evidence remains usable after temporary workspaces are deleted.

#### 10. Reliability statistics and bounded repeat planning

**Dependencies:** 6–9. **Delivers:** F5 and computed comparison views for F3.

- [ ] Publish repeat counts, independent task counts, missing-data counts, timing spread, and confidence-method metadata.
- [ ] For one fixed task's repeated binary outcome, use a 95% Wilson interval labeled as repeat reliability. For suite quality/timing, bootstrap task-level means by task clusters with an injected random generator; paired comparisons resample matched tasks together.
- [ ] Keep fixed-repeat experiments as the default. For fewer than five distinct tasks, suppress suite confidence claims and label the evidence insufficient; five is a display minimum, not a guarantee of precision.
- [ ] Add exploratory adaptive mode with predeclared minimum/maximum repeats, precision target, deterministic next-task selection, and total time/token caps. Persist the rule and every stopping decision.
- [ ] Label adaptively stopped fixed-sample intervals descriptive and disable automatic regression verdicts for adaptive runs. Do not claim sequentially valid confidence without implementing and validating a suitable method in a future revision.

**Acceptance:** Deterministic fixtures verify all-pass/all-fail bounds, missing data, unequal task counts, paired comparisons, low-sample behavior, and stop-at-budget behavior. Extra repeats cannot erase previous failures or change the selected evaluation set. Renderers perform no statistical calculations.

#### 11. Checkpoint, resume, and targeted reruns

**Dependencies:** 6–10. **Delivers:** F7 and F6 rerun execution.

- [ ] Persist the complete task/repeat schedule and per-task states before execution; checkpoint each terminal attempt and shutdown.
- [ ] Add an exclusive run lock, committed-manifest validation, and recovery for an interrupted artifact write. Keep the index rebuildable from committed run manifests.
- [ ] Resume only absent or interrupted work after verifying fixture/config/target digests; restart an interrupted task in a fresh workspace with a new attempt ID. Preserve its previous partial transcript.
- [ ] Never automatically rerun completed incorrect solutions, completed infrastructure failures, or deliberate skips; expose explicit selection for diagnostic reruns.
- [ ] Store reruns as linked child runs, retaining original scores. Mark any rerun informed by revealed hidden evidence as diagnostic.
- [ ] Reacquire and warm models for the new session, recording new lifecycle timing. Old lease records are not authority to unload an instance; uncertain orphan ownership is reported for reconciliation.

**Acceptance:** Simulated interruption before/after every commit boundary produces no duplicate completed samples or lost committed evidence; a second writer is rejected; changed task/profile digests reject resume before execution. A resumed fake run yields the same completed-task scores as an uninterrupted run, with expected differences in session/attempt timing and provenance.

#### 12. Guided setup, dashboard, and failure inspection

**Dependencies:** 7–11. **Delivers:** F1, F3, F6 user interfaces.

- [ ] Add a TUI flow for target selection, preset/task selection, budgets, execution-policy preflight, and review; offer equivalent plain CLI options.
- [ ] Define Quick Check as a small, explicitly labeled diagnostic subset; Coding Quality selects B1/B2/B3; Full Evaluation selects all five new suites plus the original three scenarios. Display task counts and capability-based skips before starting.
- [ ] Estimate runtime as a range from comparable completed history; use `unknown` when unavailable. Include load/warmup/checker overhead in ETA, separately from measured inference speed.
- [ ] Extend Results with comparable-run filters, success-versus-task-latency and success-versus-throughput views, sample counts, uncertainty, and explicit missing metrics. Keep rankings separate by execution kind.
- [ ] Extend Transcript with assertion details, bounded diffs/logs, failure filtering, and a targeted-rerun action; load large evidence lazily.
- [ ] Keep keyboard navigation, no-color labels, compact-terminal layouts, lossless resize, cancellation, and 75 ms stream buffering. Supply a table equivalent for every plot and export computed views to existing formats.

**Acceptance:** Pilot tests cover setup/back navigation, invalid configurations, unknown ETA, small terminals, no-color output, failed-model visibility, evidence inspection, and rerun selection. Plain/TUI runs built from the same profile create equivalent plans/results. A synthetic 1,000+ deltas/second stream remains responsive during inspection and resize.

#### 13. Additional model providers and Codex adapter

**Dependencies:** 6–12. **Delivers:** F8; all five benchmarks available to compatible targets.

- [ ] Implement a generic OpenAI-compatible inference adapter, then an Ollama adapter, behind the existing provider seam. Publish a capability matrix for discovery, streaming, tool use, usage, and lifecycle ownership; unsupported operations stay explicit.
- [ ] Introduce `AgentExecutor` separately from `Provider`: inspect capabilities/version, execute a task with workspace and budgets, emit observable events/artifacts, cancel, and clean up. The runner retains scheduling, evaluation, measurement, and persistence ownership.
- [ ] Implement the Codex adapter through a documented noninteractive interface after verifying the locally installed version and current official documentation. Pin supported event schemas and store raw redacted event fixtures for contract tests; do not hard-code unverified command flags in this plan.
- [ ] Capture agent version, actual/reported model identity, instructions/config fingerprint, enabled tools, execution policy, and event availability. Unknown underlying model identity prevents model-specific baseline claims.
- [ ] For tool-free suites, require an enforceable tool-disabled agent profile; otherwise mark the target unsupported. Use separate labeled profiles for agentic suites, with equivalent task budgets and checker contracts.
- [ ] Add fake process/HTTP fixtures for startup failure, auth error, invalid events, missing usage, timeout, cancellation, and leaked-child detection. Fetch current library/API documentation during implementation before choosing concrete SDK/CLI syntax.

**Acceptance:** All adapters pass common observable contracts offline; no unsupported lifecycle operations are guessed; missing timing metrics render `n/a`; Codex cancellation cleans up its processes and preserves partial evidence. Manually verify one real run per supported adapter and one real Codex repository task before advertising that adapter as validated, recording exact versions and available measurements.

### Proposed command surface

These examples define intended UX; implementing parsers/help and documentation is part of the milestones above. `--profile` selects the reproducible configuration; `--preset` selects a versioned suite/task selection. If both are used, the explicit preset overrides the profile's selection and the resolved result is persisted.

```bash
llmsweep benchmarks list
llmsweep benchmarks inspect code-edge
llmsweep benchmarks validate ./benchmarks/my-pack
llmsweep profiles list
llmsweep profiles save local-coding --from-run ./saved-run
llmsweep run --plain --models MODEL --preset coding-quality --repeat 3
llmsweep run --plain --models MODEL --profile local-coding --pack ./benchmarks/my-pack
llmsweep run --plain --models MODEL --scenarios constraint-plan,knowledge-cal
llmsweep resume ./saved-run --plain
llmsweep rerun ./saved-run --sample SAMPLE_ID --plain
llmsweep compare ./run-a ./run-b --plain
llmsweep run --target agent:codex --agent-profile codex-controlled --scenarios repo-issue
```

Preserve existing selection behavior and `--task` semantics. Reject conflicting model/provider and agent-target options before execution. Exact pack/profile formats and CLI error cases must be specified and tested in milestones 6–7 before downstream interfaces depend on them.

### Validation and release gates

Automated tests remain deterministic and network-free. Inject clocks, sleeps, IDs, random generators, filesystem roots, transports, and process runners at I/O boundaries. Separate model inference from evaluator tests so the entire scoring corpus can be validated without an installed model.

| Area | Required evidence |
| --- | --- |
| Backward compatibility | Schema 1 read/export fixtures, unchanged original scenario scores/defaults, existing flag/exit-code coverage. |
| Data integrity | Schema migration, task/attempt filename collisions, secret scrubbing, commit interruption recovery, exclusive-writer behavior. |
| Benchmark correctness | Reference solutions, negative variants, independent oracle checks, content/provenance review, and fixed task-set hashes. |
| Execution | Network/host-file isolation, protected checker boundaries, tool permissions, resource budgets, and process cleanup. |
| Statistics | Known-value fixtures, task-cluster resampling, matching/denominator rules, low-sample handling, and bounded adaptive scheduling. |
| Interfaces | CLI parser/help/export coverage and TUI pilot coverage, including high-rate events and cancellation. |
| Adapter compatibility | Fake HTTP/process contract suites plus separately recorded manual live smoke runs. |

For each milestone run applicable offline tests, `uv run ruff check`, `uv run ruff format --check`, and `uv run mypy --strict`. Before release, run the full suite, build/install the wheel in a clean environment, exercise plain CLI smoke commands, and retain the supported macOS/Ubuntu and Python/Textual compatibility matrix. Tests validate behavior rather than dataclass defaults or field forwarding.

Update README, CHANGELOG, Mintlify navigation/pages, CLI reference, architecture, metrics/scoring, and scenario documentation as behavior ships. Regenerate TUI screenshots from deterministic fixtures and label them as demonstrations. Validate docs and links when documentation pages change. Do not claim real model/agent performance from fake-provider fixtures.

### Delivery order, risks, and completion criteria

Suggested increments: milestones 6–8 establish reproducible packs and three tool-free suites; milestone 9 adds repository tasks; milestones 10–12 deliver trustworthy comparisons and usable long-run workflows; milestone 13 expands targets. This is dependency sequencing, not a calendar commitment. Re-estimate after the schema and isolation prototypes are complete.

| Risk | Mitigation / release decision |
| --- | --- |
| Scope exceeds a small local benchmark tool | Keep initial corpora and evaluator types bounded; use the existing CLI/TUI/store; defer hosted features. |
| Plausible but weak benchmark grading | Validate known incorrect solutions, use held-out task partitions and independent oracles, and review corpus ambiguity/provenance. |
| Public fixtures are memorized or exposed | Version and disclose provenance; support private user packs; label post-evidence reruns; avoid contamination-free claims. |
| Hardware or sampling differences masquerade as model improvements | Record observed settings and environment, separate comparison modes, and suppress incompatible automatic verdicts. |
| Resume or selective reruns inflate scores | Preserve immutable attempt history, explicit selection, and parent/child lineage; never replace an original failure with a diagnostic success. |
| External-agent interfaces change or hide measurements | Version adapters, retain raw event fixtures, use capability preflight, and expose unknown metrics honestly. |
| Strong isolation unavailable on a user's machine | Fail preflight for new executable packs; keep compatible v1 and tool-free workflows available. |

The expansion is complete only when F1–F8 and B1–B5 meet their milestone criteria, existing v1 behavior remains compatible, all automated gates pass, documentation distinguishes supported from unavailable capabilities, and manual live validation is recorded for each advertised execution adapter. This document itself authorizes planning only; implementation progress should be tracked by checking the milestone items as they are actually completed.
