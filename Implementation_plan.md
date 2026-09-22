# llmsweep v1 — LM Studio

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
