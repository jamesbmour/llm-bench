# llmsweep — Implementation Plan

The v1 foundation below remains the compatibility contract. The [feature and benchmark expansion](#feature-and-benchmark-expansion) tracks remaining work after the core expansion landed in-tree.

**Expansion status:** in progress (September 22, 2026). Milestones 1–5 and 6–11 are complete in source. Milestone 12 (guided setup UI, comparison dashboard, failure inspection) remains. Milestone 13 (additional providers and Codex) is out of scope. See `Implementation_plan_new_fetures.md` for the per-feature checklist.

## v1 foundation — LM Studio

## Summary

Build a new Python 3.11+ CLI and Textual application for LM Studio on macOS and Linux. Support all three benchmarks, repeat measurements, saved runs, transcripts, exports, and baseline comparisons.

Use the existing `lmstudio_agent_bench.py` only as LM Studio integration guidance. The brief defines scoring; the new implementation will have independent architecture and tests.

Default to all three scenarios, serial execution, and one load/warmup per model followed by all repeats. Defer Ollama, generic OpenAI, billing, and provider switching.

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

Five additional benchmarks are implemented and opt-in; default runs still select `weather`, `agent-code`, and `codegen`. Existing flags, exit codes, saved runs, and score meanings remain compatible.

Non-goals for this expansion: a hosted leaderboard, billing or price estimates, arbitrary remote plugin execution, a browser dashboard, automatic publication, importing the entire SWE-bench dataset, and additional inference providers (OpenAI-compatible, Ollama) or external agent adapters (Codex).

### Remaining feature work

| ID | Feature | Status |
| --- | --- | --- |
| F1 | Guided Benchmark Setup | Presets and a plan screen exist; full setup flow, profile save, and pilot coverage remain. |
| F2 | Custom Benchmark Packs | Validation and an example pack exist; end-to-end pack runs and authoring docs remain. |
| F3 | Quality–Speed Comparison Dashboard | `compare_checked` and `llmsweep compare` exist; TUI views and comparison export remain. |
| F4 | Reproducible Run Profiles | Core profiles, schema 2, and fingerprints are shipped; docs and extra acceptance tests remain. |
| F5 | Reliability and Confidence Reporting | Statistics are computed and stored; TUI surfacing and stop-rule tests/docs remain. |
| F6 | Failure Analysis and Targeted Reruns | Categories, evidence, and `rerun` exist; transcript inspection UI and lineage tests remain. |
| F7 | Checkpoint and Resume | Schedule, lock, and `resume` exist; interruption tests and docs remain. |
| F8 | Model and Agent Adapters | **Out of scope** (OpenAI, Ollama, Codex). |

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

### Built-in benchmarks (shipped)

B1–B5 are implemented as opt-in suites: `code-edge` (20 tasks), `multi-file` (6), `repo-issue` (8), `constraint-plan` (30), and `knowledge-cal` (100). Corpus details, budgets, and oracles live in `src/llmsweep/benchmarks/corpora/`. User-facing scoring notes still belong in Mintlify docs.

### Execution and evaluator isolation

Broader code tasks must not inherit a misleading sandbox claim from v1's temporary directories and process groups. Introduce an explicit execution policy before exposing custom executable tasks or external agents:

- Candidate processes run in disposable isolated environments with no host-home mounts, no inherited credentials, no network by default, bounded CPU/memory/processes/output/time, and only the task workspace writable. On macOS this may require a container VM; capability detection must report unavailable prerequisites.
- The trusted controller holds the answer key, hidden inputs, and scoring logic outside candidate access. For function tasks, send individual inputs to isolated candidate processes and compare outputs in the controller. For repository tasks, evaluate the patch in a fresh environment and independently validate structured test results; never trust a success marker printed by candidate code.
- Public development tests may be exposed; private grading details are accessible only after the scored attempt closes. A diagnostic rerun after revealing those details is marked as such and excluded from untouched held-out comparisons.
- Custom packs initially choose approved evaluator types and parameters. Do not dynamically import Python from manifests, interpolate shell commands, or automatically execute pack install hooks. Validate archive paths, symlinks, file counts, sizes, hashes, and allowed writes before materialization.
- For remote inference, a trusted controller may use credentials for the configured origin. Candidate tools remain network-disabled. An external agent's required inference connection must be narrowly allowed and logged separately from tool/browser access; if that separation cannot be enforced, report an unsupported execution policy.
- Preserve the existing v1 workflow-confinement mode for compatibility. New executable packs require the stronger policy and fail preflight with a setup error if it is unavailable; do not silently downgrade it.

### Implementation milestones

Milestones 6–11 are complete in source. Milestone 13 is out of scope. Only milestone 12 remains.

#### 12. Guided setup, dashboard, and failure inspection

**Dependencies:** 7–11. **Delivers:** F1, F3, F6 user interfaces.

- [ ] Finish the TUI setup flow (target selection, budgets, profile save, back navigation); presets and `llmsweep setup` already exist.
- [ ] Show capability-based skips and historical runtime ranges in setup (not just task counts and `unknown` ETA).
- [ ] Extend Results with comparable-run filters, success-versus-task-latency and success-versus-throughput views, sample counts, uncertainty, and explicit missing metrics. Keep rankings separate by execution kind.
- [ ] Extend Transcript with assertion details, bounded diffs/logs, failure filtering, and a targeted-rerun action; load large evidence lazily.
- [ ] Keep keyboard navigation, no-color labels, compact-terminal layouts, lossless resize, cancellation, and 75 ms stream buffering. Supply a table equivalent for every plot and export computed views to existing formats.

**Acceptance:** Pilot tests cover setup/back navigation, invalid configurations, unknown ETA, small terminals, no-color output, failed-model visibility, evidence inspection, and rerun selection. Plain/TUI runs built from the same profile create equivalent plans/results. A synthetic 1,000+ deltas/second stream remains responsive during inspection and resize.

### Command surface

Shipped commands below; milestone 12 covers the remaining TUI surfaces. `--profile` selects the reproducible configuration; `--preset` selects a versioned suite/task selection. If both are used, the explicit preset overrides the profile's selection and the resolved result is persisted.

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
llmsweep setup --preset quick-check
```

Preserve existing selection behavior and `--task` semantics. Reject conflicting model/provider options before execution.

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

For each milestone run applicable offline tests, `uv run ruff check`, `uv run ruff format --check`, and `uv run mypy --strict`. Before release, run the full suite, build/install the wheel in a clean environment, exercise plain CLI smoke commands, and retain the supported macOS/Ubuntu and Python/Textual compatibility matrix. Tests validate behavior rather than dataclass defaults or field forwarding.

Update README, CHANGELOG, Mintlify navigation/pages, CLI reference, architecture, metrics/scoring, and scenario documentation as behavior ships. Regenerate TUI screenshots from deterministic fixtures and label them as demonstrations. Validate docs and links when documentation pages change. Do not claim real model/agent performance from fake-provider fixtures.

### Delivery order, risks, and completion criteria

Finish milestone 12 (guided setup UI, comparison dashboard, failure inspection) and the remaining docs/tests listed in `Implementation_plan_new_fetures.md`.

| Risk | Mitigation / release decision |
| --- | --- |
| Scope exceeds a small local benchmark tool | Keep initial corpora and evaluator types bounded; use the existing CLI/TUI/store; defer hosted features. |
| Plausible but weak benchmark grading | Validate known incorrect solutions, use held-out task partitions and independent oracles, and review corpus ambiguity/provenance. |
| Public fixtures are memorized or exposed | Version and disclose provenance; support private user packs; label post-evidence reruns; avoid contamination-free claims. |
| Hardware or sampling differences masquerade as model improvements | Record observed settings and environment, separate comparison modes, and suppress incompatible automatic verdicts. |
| Resume or selective reruns inflate scores | Preserve immutable attempt history, explicit selection, and parent/child lineage; never replace an original failure with a diagnostic success. |
| Strong isolation unavailable on a user's machine | Fail preflight for new executable packs; keep compatible v1 and tool-free workflows available. |

The expansion is complete when F1–F7 meet their remaining criteria in `Implementation_plan_new_fetures.md`, existing v1 behavior remains compatible, and all automated gates pass. LM Studio remains the only supported provider.
