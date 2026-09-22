---
title: "Implementation design and assumptions"
description: "Verified LM Studio v1 scope, measurement rules, lifecycle ownership, and release assumptions."
---

# Implementation design and assumptions

This release implements LM Studio only, following the revised scope in
`Implementation_plan.md`. The original script is unchanged and is used only as
provider integration guidance. The tests and scenario specification define scoring.

## Execution and measurement

Serial execution avoids competing loads and contention for unified memory.
`--parallel N` permits concurrency only when every selected model is already loaded;
those measurements are marked contended and excluded from regression verdicts.
A model is loaded and warmed once, then each repeat starts a fresh scenario.

`TurnRecorder` measures TTFT from provider invocation to the first nonempty output
delta (text, reasoning, or tool call). Role headers and final usage packets are not
output. Generation time is the first-to-last output interval. Load, warmup, tool
execution, checker execution, and trailing usage delivery are excluded. LM Studio
has no authoritative generation duration here, so these are client-observed timings.
Single-delta output has no measurable generation interval and reports `n/a` throughput.

Completion-token usage includes reasoning when the provider includes it. API total
tokens are stored separately from output tokens. Missing usage falls back to
`ceil(UTF8 output bytes / 4)`, including reasoning and tool argument/name fragments.
This estimate is independent of network chunking and is always flagged `estimated`;
it is not an equivalent tokenizer measurement. Mixed token sources are shown, and
baseline comparisons skip mixed or different sources.

Multi-turn sample throughput divides total output tokens by total generation time;
it is unavailable if any turn lacks a measurable interval. Each scenario reports
mean, median, and nearest-rank p95 across complete repeats. Median helps expose
outliers' effect on the mean; p95 shows slower-tail behavior without hiding it in
an average. Small sample counts make p95 coarse. Model throughput is explicitly the
**mean of scenario means**, so lengthy coding completions do not dominate weather.

Deterministic failures are benchmark outcomes, not transport failures. An incorrect
answer can produce exit 0 with a failed score. Errors remain visible as rows. Only
`--fail-on-regression` changes comparable threshold regressions to exit 3.

## LM Studio integration

Discovery uses `/api/v1/models`, falling back to `/api/v0/models` only on 404, 405,
or 501. Authentication errors and transport failures never trigger fallback.
The native v1 spelling is `embedding`; v0 uses `embeddings`. Both, plus `reranker`,
are excluded by type. Names are never used to exclude embeddings: an `embed`-named
model reporting `llm` remains eligible. Unknown capability is distinct from false.

Streaming uses `/v1/chat/completions`. The byte parser handles fragmented UTF-8 and
SSE framing, comments, indexed fragmented tool calls, interleaved text/reasoning/tools,
and usage packets with empty choices. Bare NDJSON intentionally yields no SSE deltas.
Read timeouts are per-request inactivity limits. Connection failures and retryable
server errors use three attempts with jitter; streams emitting output never restart.

Load POST success is not readiness. The provider records its returned instance ID,
then polls discovery every second within `--load-timeout` (maximum 600 seconds).
Reported `load_time_seconds` takes precedence over measured readiness wall time.
Preloaded and v0 models have `load_s: null` and an explanatory lifecycle note.
Only returned IDs owned by this run may be unloaded. Cancellation waits for the
in-flight load attempt to resolve before cleanup; this can take up to the load ceiling.
Unload is verified. An uncertain load without an ID is reported as uncertain rather
than unloading a model by name. Lifecycle mutations are not blindly retried.

## Store and presentation

The runner owns storage so the CLI and TUI cannot accidentally implement different
scoring or serialization. A fixed-clock/fixed-ID integration test asserts identical
plain and TUI run bytes. Real independently executed runs naturally have different
IDs and timing observations.

Files are canonical JSON, atomically replaced with a temporary sibling and fsync.
The index uses a filesystem lock. Schema version 1 is the first published format;
future versions and unversioned reference output are refused. Future changes must
supply explicit older-schema migrations instead of guessing missing data.

```text
<run-store>/
  index.json
  runs/<UTC timestamp>-<short unique suffix>/
    run.json
    transcripts/<provider>_<model>-<identity hash>_<scenario>_r<N>.json
```

The identity hash prevents sanitized filenames from colliding. Baselines use the
full `(provider, id)` pair. `--transcript-dir` gets an additional run-ID directory.
ETA appears only when matching historical settings/model durations exist.

Textual is a view over `RunSession`. All network work belongs to App-owned async
`@work(exit_on_error=False)` workers. Per-model worker handles isolate reruns;
`exclusive=False` prevents a rerun from cancelling siblings. Worker error state
surfaces its exception on that model; cancel state persists partial results.
HTTP and checker subprocesses yield to the event loop. No transient widget owns a run.
Deltas buffer until the 75 ms timer, bounding widget updates during high-speed bursts.
Resizing reflows existing buffers. Textual's HelpPanel and command palette are retained.

Non-TTY input/output, `CI`, `TERM=dumb`, and `--plain` select plain rendering before
constructing the App. Colors respect `NO_COLOR`. Keys are redacted at output boundaries,
including serialized exports. Redirects and environment proxies are disabled on the
provider client so bearer credentials stay on its configured origin.

## Assumptions and resolved ambiguities

- LM Studio is the only registered provider in v1. Other provider selections and
  cost sorting are usage errors. No billing or cost-projection implementation is claimed.
  Future Ollama support should use its native NDJSON endpoint for generation/load
  nanosecond timings; the OpenAI-compatible endpoint would discard that distinction.
- The latest user instruction makes the reference script provider guidance only.
  The explicit weather, summation, and Fibonacci assertions define the new scores.
- All three scenarios are the default. A custom task narrows to weather and disables
  scoring. Known no-tool models skip tool scenarios; unknown capability is attempted.
- Weather time comes from the real timezone clock; the deterministic score checks
  called tool names and the specified temperature regex, not the exact clock text.
- `expected_tools` means scoring-required tool names: three for weather, `write_file`
  for agent-code, none for codegen or skipped scenarios.
- The `e` picker toggle can only narrow eligible models to explicitly known chat types;
  it never makes embeddings runnable. Provider switching is omitted while only one exists.
- Preloaded models have no new load measurement; `n/a` is more honest than zero.
  `--keep-loaded` and `--no-unload` are aliases and intentionally retain owned models.
- A codegen fallback without fences is accepted only if the whole answer is executable
  Python and passes the checker. Unfenced prose fails. No negative-input or fib(100)
  assertion is added beyond the requested scoring contract.
- Temporary directories and guarded tools constrain the workflow, but generated Python
  is not an OS sandbox. It runs with the user's permissions. Checkers have sanitized
  environments, bounded captured output, process-group termination, and timeouts.
- Explicit JSON/CSV/Markdown destinations are authorized writes in addition to the run
  store, transcript directory, and temporary workspaces. Offline `show` is read-only;
  export writes only to requested paths.
- Resume means viewing a persisted partial run; automatic continuation of interrupted
  conversations is not implemented. Reopening a run does not connect to LM Studio.

## Verification

Offline HTTP fixtures exercise both LM Studio discovery dialects, fragmented SSE,
reasoning, usage-only chunks, invalid arguments, unknown tools, errors, retries,
interrupted streams, readiness cleanup, and preloaded ownership. Tests disable sockets.
Pilot tests assert state during cancellation, worker errors, concurrent reruns,
stream buffering, resizing, offline dialogs, and plain/TUI serialization parity.
The README screenshot is generated using `App.export_screenshot()` from labeled fixtures.
