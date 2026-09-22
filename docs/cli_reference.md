---
title: "CLI Reference & Configuration"
description: "Command documentation, flags, environment variables, configuration precedence, and exit codes"
---

## CLI Command & Flag Reference

Use `llmsweep COMMAND [OPTIONS]`. Omitting the command selects `run`.
Top-level `--list` and `--show PATH` are compatibility aliases. Use
`llmsweep run --help` for argparse's full usage.

| Command | Purpose |
| --- | --- |
| `run` | Benchmark selected models; opens the picker in an interactive terminal. |
| `list` | List eligible chat models, largest parameter count first. |
| `show PATH` | Open a run directory or `run.json` offline; `--plain` prints tables. |
| `export PATH --json FILE --csv FILE --markdown FILE` | Export one or more formats offline. |
| `doctor` | Check discovery, authentication, API version, store writability, and execution policy without chat. |
| `providers` | Show the supported provider: `lmstudio`. |
| `benchmarks list` | List built-in suites, task counts, and execution kind. Offline. |
| `benchmarks inspect NAME` | List task ids and content digests for one suite. |
| `benchmarks validate PATH` | Check a local pack manifest and fixture hashes. Packs are never auto-loaded. |
| `profiles list` / `show` / `save NAME --from-run PATH` | Named non-secret run profiles. |
| `setup` | Print the resolved preset, task count, isolation preflight, and `eta unknown`. |
| `resume PATH` | Continue a schema-2 run after checking its fingerprint. |
| `rerun PATH --sample ID` | Start a diagnostic child run for one saved sample. |
| `compare PATH PATH` | Print comparison eligibility. Schema 1 runs keep the original metric comparison. |

### Subcommand Details

#### `llmsweep run`

Benchmarks selected models against all three scenarios (`weather`, `agent-code`, `codegen`). In an interactive terminal, opens the Textual model picker for selection. With `--plain` or in non-interactive environments (pipes, CI), requires explicit `--models` or `--all`.

Connection flags (`--base-url`, `--host`, `--port`, `--api-key`) and provider flags apply to this command. Export flags (`--json`, `--csv`, `--markdown`), baseline flags (`--baseline`, `--fail-on-regression`), and run-store overrides also apply here.

#### `llmsweep list`

Lists all eligible chat models discovered from LM Studio, sorted by parameter count (largest first). Non-chat types (embeddings, rerankers) are excluded. Supports the same connection flags as `run`. Use `--require-tool-use` to filter to tool-capable models only, and `--exclude a,b` to omit specific model IDs or substrings.

#### `llmsweep show PATH`

Opens a previously saved run for offline inspection — no LM Studio connection required. Accepts either a run directory path (reads `<dir>/run.json`) or a direct JSON file path. Use `--plain` for ANSI-free tabular output, and `--sort-by KEY` to control model ordering (`order`, `tok_s`, `ttft`, `total`, `load`, `model`).

#### `llmsweep export PATH`

Exports an offline run (opened by directory or JSON file) to one or more formats. Specify any combination of `--json FILE`, `--csv FILE`, and `--markdown FILE`. All exports apply credential redaction automatically. See [Data Formats & Exports](/data_formats) for the complete schema reference.

#### `llmsweep doctor`

Performs a diagnostic check without executing any benchmarks:

1. **Discovery**: Connects to LM Studio and lists available models, reporting the API version (v1 or v0 fallback).
2. **Authentication**: Verifies that credentials are accepted by the server (if configured).
3. **API Version**: Confirms which discovery endpoint is active (`/api/v1/models` vs `/api/v0/models`).
4. **Store Writability**: Checks that the run store directory is writable and the index lock can be acquired.

This command never sends chat completions or loads models — it's safe to run in CI pipelines for pre-flight validation. Exit code 2 indicates a configuration or connection problem; exit code 4 indicates an authentication failure.

#### `llmsweep providers`

Lists all registered provider backends supported by this version of `llmsweep`. In v1, only `lmstudio` is implemented. Ollama, generic OpenAI, and OpenRouter are deferred to future releases.

---

### Flag Reference

| Flag | Default / meaning |
| --- | --- |
| `--models SPEC`, `--all` | IDs, indices/ranges, or unique substrings; mutually exclusive. Plain runs require one. |
| `--provider NAME`, `--providers NAMES` | Only `lmstudio` is implemented in v1. |
| `--base-url URL` | Server origin; defaults to `http://localhost:1234`. |
| `--host HOST`, `--port PORT` | Alternative to `--base-url`; default `localhost`, `1234`. |
| `--api-key KEY` | Optional credential; prefer environment variables to avoid shell history. |
| `--require-tool-use` | Select models advertising tool support. |
| `--exclude a,b` | Exclude matching model IDs/substrings. |
| `--scenarios LIST` | `weather,agent-code,codegen`; preserves order and deduplicates. |
| `--task TEXT` | Custom weather task, scored `n/a`; rejects explicitly selected other scenarios. |
| `--repeat N` | Repeats per scenario; default `1`. |
| `--max-turns N` | Override weather's 6 and agent-code's 10 turns; codegen stays single-turn. |
| `--max-tokens N` | Completion limit; default `1024`. |
| `--timeout SECONDS` | Per-request inactivity timeout; default `300`. |
| `--load-timeout SECONDS` | Load/readiness ceiling; default and maximum `600`. |
| `--no-warmup` | Skip the one 8-token warmup request per model. |
| `--no-unload`, `--keep-loaded` | Retain instances loaded by the run. Pre-existing instances are always retained. |
| `--parallel N` | Default `1`; values above 1 require preloaded models and mark timings contended. |
| `--plain`, `--no-color` | Plain mode / monochrome TUI; `NO_COLOR` is honored. |
| `--theme NAME` | Textual theme for the TUI (`run`, `show`); default `textual-dark`, `Ctrl+T` cycles at runtime. |
| `--verbose` | Include tool events in stderr or the TUI log. |
| `--transcript-dir DIR` | Additional transcript destination, grouped by run ID. |
| `--sort-by KEY` | `order`, `tok_s`, `ttft`, `total`, `load`, `model`; `cost` is rejected in LM Studio v1. |
| `--json PATH`, `--csv PATH`, `--markdown PATH` | Explicit export destinations. |
| `--baseline PATH` | Compare with a saved run directory or JSON file. |
| `--fail-on-regression` | Exit 3 for comparable regressions beyond thresholds; requires a baseline for runs. |
| `--run-store DIR` | Override the platform data directory. |
| `--config PATH` | Explicit TOML file instead of the project `llmsweep.toml`. |
| `--list`, `--show PATH` | Top-level compatibility aliases. |

Connection flags apply to `run`, `list`, and `doctor`; exports and baseline flags
apply to `run`, `show`, and `export`. Unknown or inapplicable flags are usage errors.

---

## Model Selection Syntax

When specifying `--models <spec>`, `llmsweep` resolves terms using a deterministic priority hierarchy:

1. **Exact Ref**: Matches `provider:id` (e.g. `lmstudio:qwen2.5-coder-7b-instruct`) or exact bare ID.
2. **1-Based Index Ranges**: Matches positions from `llmsweep list`. Supports single indices (`1`), comma-separated lists (`1,3,5`), and continuous hyphenated ranges (`1-3,5-7`).
3. **Unique Substring**: Case-insensitive substring match (e.g. `coder-7b`). Ambiguous matches raise a descriptive error listing all candidates without guessing.

Multiple terms can be combined with commas: `--models "qwen2.5-coder-7b-instruct,1-3"`. Results are deduplicated while preserving user-specified execution order.

---

## Interactive Terminal UI (Textual)

Running `llmsweep run` in an interactive terminal opens the full Textual TUI. Plain mode is automatically selected when stdout is piped, `CI` is set, or `TERM=dumb`. Use `--plain` to force it explicitly.

### TUI Screens

| Screen | Purpose |
| --- | --- |
| **Model Picker** | Interactive table of discovered models with search filtering, bulk select/clear, parameter sizes, tool-capability indicators, and loaded-state columns. |
| **Live Runner** | Real-time progress bar, per-model streaming cards (buffered at 75 ms intervals), throughput sparklines, tool-call timeline tables, card zoom navigation, and verbose log pane (`--verbose`). |
| **Results Dashboard** | Sortable summary table with TTFT, tok/s, pass/fail status, color-coded regression arrows. Supports text filtering, status filtering (all/passed/failed/errors), baseline diff view, export dialog, and per-model rerun. |
| **Transcript Viewer** | Full conversation history for a specific model/repeat with formatted tool JSON. Navigate repeats with `n`/`p`, copy to clipboard with `y`. |
| **Help Overlay** (`F1` / `?`) | Lists every keyboard binding active on the current screen, plus the metric legend (TTFT definition, token source meanings) and status/exit-code reference. |

### Keyboard Shortcuts

#### Global Bindings

| Shortcut | Action | Scope |
| --- | --- | --- |
| `Ctrl+Q` | Clean up run-owned instances and quit | All screens |
| `Ctrl+X` / `Esc` on Live | Cancel current run, save partial results, unload owned models | Live screen only |
| `Ctrl+C` | Show quit/cancel reminder (does not immediately exit) | All screens |
| `Ctrl+T` | Cycle color theme (`textual-dark`, `nord`, `tokyo-night`, etc.) | All screens |
| `F1` / `?` | Toggle help overlay with active bindings and metric legend | All screens |

#### Model Picker

| Shortcut | Action |
| --- | --- |
| `Space` / `Enter` on table row | Toggle model selection |
| `a` | Select all visible (filtered) models |
| `x` | Clear current selection |
| `/` | Focus search input to narrow the list |
| `t` | Toggle tools-only filter (models advertising tool support) |
| `e` | Narrow to explicitly known chat types only (`llm`); embeddings/rerankers excluded |
| `Ctrl+R` | Start benchmark on selected models |

#### Live Runner

| Shortcut | Action |
| --- | --- |
| `[` / `]` | Navigate to previous/next model card |
| `z` | Zoom the focused card to full screen (press again to restore) |
| `Esc` | Cancel run and return to results |

#### Results Dashboard

| Shortcut | Action |
| --- | --- |
| `s` | Cycle sort column (`order`, `tok_s`, `ttft`, `total`, `load`, `model`) — sorted column marked with ▲/▼ |
| `/` | Focus text filter (matches model name, status, or error) |
| `f` | Cycle status filter (all → passed → failed → errors) |
| `Enter` on row | Open transcript viewer for selected model |
| `d` | Show baseline comparison diff (prompts for baseline path if not yet loaded) |
| `e` | Export results to JSON, CSV, or Markdown via path dialog |
| `r` | Rerun the selected model in-place (preserves other models' results) |

#### Transcript Viewer

| Shortcut | Action |
| --- | --- |
| `n` / `p` | Navigate to next/previous repeat |
| `y` | Copy current transcript JSON to clipboard |

---

## Configuration & Precedence

Precedence is CLI > `LLMSWEEP_*` environment > project `./llmsweep.toml` >
`platformdirs.user_config_path("llmsweep") / "llmsweep.toml"` > defaults.
An explicit `--config PATH` replaces the project file. Keys are validated;
unknown keys and literal `api_key` values are errors. Provider settings use
`[providers.lmstudio]`. Deferred provider blocks are not activated in this release.

```toml
repeat = 3
scenarios = "weather,agent-code,codegen"
require_tool_use = false
keep_loaded = false

[providers.lmstudio]
base_url = "http://localhost:1234"
timeout = 300.0
load_timeout = 600.0
api_key_env = "LMSTUDIO_API_KEY"

[thresholds]
tok_s_regression_pct = 5.0
ttft_regression_pct = 5.0
```

| Environment variable | Meaning |
| --- | --- |
| `LMSTUDIO_API_KEY` | Preferred provider credential; `LLMSWEEP_API_KEY` is the fallback. |
| `LLMSWEEP_BASE_URL` | Server URL. |
| `LLMSWEEP_HOST`, `LLMSWEEP_PORT` | Hostname and port, when no base URL is configured. |
| `LLMSWEEP_TIMEOUT`, `LLMSWEEP_LOAD_TIMEOUT` | Request and readiness timeouts. |
| `LLMSWEEP_RUN_STORE` | Persistent run directory. |
| `LLMSWEEP_PLAIN` | Boolean (`true`, `false`, `1`, `0`, `yes`, `no`). |
| `NO_COLOR` | Disable colors in both renderers. |

Other root settings use the corresponding `LLMSWEEP_` uppercase name (e.g. `LLMSWEEP_REPEAT`, `LLMSWEEP_SCENARIOS`, `LLMSWEEP_SORT_BY`, `LLMSWEEP_THEME`).
Credentials are redacted in error messages, logs, transcripts, and exports.
Redirects are disabled so credentials stay on the configured provider origin.

---

## Exit codes

| Code | Meaning | Cause |
| --- | --- | --- |
| **0** | Success | Execution completed; a deterministic scoring failure alone does not change the exit code. |
| **1** | Model Error | One or more models encountered an unrecoverable execution or API error, or the run was cancelled. |
| **2** | Setup / Config Error | Invalid flags, unparseable model/scenario expressions, missing config file, or initial connection failure. |
| **3** | Regression Failure | Comparable regression beyond the configured threshold under `--fail-on-regression`. |
| **4** | Auth Failure | Non-retryable authentication or authorization error (HTTP 401/403). |

<Note>
A model that produces a wrong answer is reported as `completed` with a lower success rate — it does **not** produce exit code 1. Only transport failures, API errors, timeouts, and configuration problems change the exit code. Use `--fail-on-regression` to gate CI pipelines on performance degradation (exit code 3).
</Note>

---

For the complete schema reference of persisted run documents, transcript files, and export formats, see [Data Formats & Exports](/data_formats).