# llmsweep

**Benchmark agentic, coding, and throughput performance of local LM Studio models.**

`llmsweep` is a modern Python 3.11+ command-line and terminal user interface (TUI) benchmarking suite designed for macOS and Linux. It evaluates local LLMs running in [LM Studio](https://lmstudio.ai/) across multi-step tool calling, real-world bug fixing, and algorithmic code generation—measuring client-observed throughput, time-to-first-token (TTFT), model loading latency, and task correctness with statistical rigor.

---

## Key Features

- **Three Dedicated Scenarios**:
  - **`weather`**: Multi-step structured tool calling with parameter extraction, validation, and regex verification.
  - **`agent-code`**: Agentic workspace inspection, file searching, guarded file editing, and test suite verification.
  - **`codegen`**: Zero-shot Python implementation from specification, verified via an isolated subprocess test runner.
- **Client-Observed Measurement Rigor**:
  - Direct SSE stream decoding across byte boundaries and split UTF-8 sequences.
  - Explicit **Time-To-First-Token (TTFT)** tracking (dispatched timestamp to first content/reasoning/tool delta).
  - Client-observed tokens/sec calculated strictly over the streaming generation window.
  - Model throughput aggregated as the **mean of scenario means**.
  - Multi-run statistical rollups: mean, median, and nearest-rank p95.
- **Lifecycle Management**:
  - Automated model loading, readiness polling, single-turn warmup, and guaranteed idempotent unloading.
  - Tracks cold/reported load times versus readiness-measured durations.
  - Retains preloaded models untouched; cleans up only run-instantiated instances.
- **Dual Interface**:
  - **Interactive TUI**: Built with [Textual](https://textual.textualize.io/) featuring model selection, live streaming dashboards, sparkline metrics, turn logs, and comparison tables.
  - **Headless Plain CLI**: Deterministic, ANSI-free output suitable for scripts, pipes, non-TTY environments, and CI pipelines.
- **Baselines & Regressions**: Compare performance against historical benchmark runs with configurable regression thresholds (default: 5% on TTFT and throughput).
- **Offline Persistence & Exports**: Fully schema-versioned runs and turn-by-turn transcripts stored locally. Export to JSON, CSV, and Markdown without contacting the server.

---

## Benchmark Scenarios

| Scenario | Mode | Objective | Evaluation & Verification |
| :--- | :--- | :--- | :--- |
| **`weather`** | Tool-Calling | Queries `get_weather` tool, handles multi-turn conversation, and reports temperatures. | Validates tool schema compliance, required-tool invocation, and regex temperature match. (Can be customized with `--task`, which disables scoring). |
| **`agent-code`** | Agentic Coding | Inspects a mock repository, reads files, performs grep searches, fixes a bug, and writes changes. | Constrained workspace (`read_file`, `list_files`, `grep_files`, guarded `write_file`). Verified by rerunning an independent test suite in an isolated subprocess. |
| **`codegen`** | Direct Coding | Implements an efficient Fibonacci function based on a strict specification (no tools). | Extracts Python code blocks (or valid unfenced Python) and runs against test cases in a bounded, sandboxed subprocess with a 15-second timeout. |

---

## Installation

### Prerequisites

- **Python**: 3.11 or later
- **Operating System**: macOS or Linux
- **LM Studio**: Running locally with the developer server enabled (default: `http://localhost:1234`).

### Using `uv` (Recommended)

```bash
# Clone the repository
git clone https://github.com/jamesbrendamour/llm-bench.git
cd llm-bench

# Install package and dependencies
uv pip install .

# For development (including pytest, textual-dev, ruff, mypy)
uv pip install -e ".[dev]"
```

### Using Standard `pip`

```bash
pip install .
```

---

## Quick Start

### 1. Interactive TUI Mode

Launch the interactive model picker to select models, choose scenarios, and watch live benchmark streaming:

```bash
llmsweep run
```

### 2. Plain Headless CLI Mode

Run all benchmarks for specific models and print results directly to the terminal:

```bash
llmsweep run --plain --models "qwen2.5-coder-7b-instruct,deepseek-coder-6.7b"
```

### 3. Test All Available Models

Benchmark every available chat model with 3 repeats per scenario:

```bash
llmsweep run --plain --all --repeat 3
```

---

## CLI Reference

### Commands

- **`llmsweep run`**: Execute benchmark scenarios against selected or all models.
- **`llmsweep list`**: List available chat models reported by LM Studio (excluding embeddings/rerankers).
- **`llmsweep show <run-id|file>`**: Inspect past benchmark results and transcripts offline.
- **`llmsweep export <run-id> --format <json|csv|md>`**: Export a stored run to JSON, CSV, or Markdown.
- **`llmsweep doctor`**: Diagnose environment setup, LM Studio server reachability, API version (v1/v0), and store permissions without running benchmarks.
- **`llmsweep providers`**: Display status of supported backend providers (LM Studio).

### `llmsweep run` Options

| Flag | Type | Description |
| :--- | :--- | :--- |
| `-m, --models <spec>` | String | Comma-separated model IDs, 1-based index ranges (e.g. `1-3,5`), or unique substrings. |
| `-a, --all` | Flag | Select all available chat models discovered from the server. |
| `-s, --scenarios <list>` | String | Comma-separated list: `weather`, `agent-code`, `codegen` (default: all three). |
| `-r, --repeat <n>` | Integer | Number of benchmark repeats per scenario (default: `1`). |
| `--plain` | Flag | Force plain text ANSI-free output instead of the interactive TUI. |
| `--task <prompt>` | String | Custom prompt for `weather` scenario (disables automatic scoring). |
| `--require-tool-use` | Flag | Filter models to only those that explicitly advertise tool-calling support. |
| `--keep-loaded` | Flag | Alias for `--no-unload`. Keeps models in memory after benchmark completion. |
| `--parallel` | Flag | Run benchmarks across models in parallel (requires instances to be preloaded). |
| `--baseline <path>` | Path | Compare results against a previous run JSON file. Flags regressions > 5%. |
| `--json <path>` | Path | Save run results to specified JSON file. |
| `--csv <path>` | Path | Save scenario summary table to CSV. |
| `--markdown <path>` | Path | Save formatted results report as Markdown. |
| `--timeout <sec>` | Integer | Request inactivity timeout in seconds (default: `300`). |
| `--load-deadline <sec>` | Integer | Maximum wait time for model loading and readiness in seconds (default: `600`). |

---

## Measurement Methodology

`llmsweep` enforces strict measurement rules to ensure benchmarks reflect true client experience rather than API optimism:

1. **Time-to-First-Token (TTFT)**:
   - Clock starts the moment the HTTP request payload is dispatched over the wire.
   - Clock stops at the arrival of the first substantive delta (content, reasoning chunk, or tool call argument). Role-only envelopes and empty usage headers are excluded.
2. **Client-Observed Throughput**:
   - Streaming window starts at the first output delta and ends at the final delta chunk.
   - Calculated as: `output_tokens / generation_window_seconds`.
   - Excludes server queuing, model loading, warmup invocation, tool execution, and local test checking time.
   - Scenario repeats are pooled; overall model throughput is the **mean of scenario means**.
3. **Token Accounting**:
   - Prefers API-reported completion token counts (including native reasoning tokens).
   - If token counts are not reported by the server, uses a chunk-independent estimate: `ceil(utf8_output_bytes / 4)`.
   - The token source (`api` or `estimated`) is explicitly tracked and displayed in all exports.
4. **Lifecycle & Isolation**:
   - Models are loaded once per benchmark session, polled for readiness, and primed with a single warmup query before repeat executions.
   - Subprocesses for `codegen` and `agent-code` run with sanitized environments, process-group limits, bounded output buffers, and strict timeouts.

---

## Interactive TUI (Textual)

Running `llmsweep run` in an interactive terminal opens the full TUI:

- **Model Picker**: Search, inspect parameter sizes, check readiness status, and select models via spacebar or numeric shortcuts.
- **Live Runner**: Real-time progress bars, streaming response views (buffered at 75 ms intervals to prevent UI stutter), and per-turn latency metrics.
- **Results Dashboard**: Summary tables with TTFT, tok/s, pass/fail status, and color-coded regression arrows paired with descriptive text.
- **Transcript Viewer**: Deep-dive into raw turn messages, structured tool requests, simulated tool returns, and model completions.

### TUI Keyboard Shortcuts

| Key | Action |
| :--- | :--- |
| `Space` | Toggle model selection in Picker |
| `Enter` | Confirm selection / Start benchmark |
| `Tab` / `Shift+Tab` | Navigate between screens and panels |
| `Ctrl+C` | Cancel active model benchmark (preserves partial results and safely unloads) |
| `Ctrl+Q` | Quit application |
| `?` | Toggle Help panel |

---

## Baselines & Regression Testing

Track model degradation across updates or parameter quantization by asserting against baselines:

```bash
# Save baseline run
llmsweep run --plain --models qwen2.5-coder-7b-instruct --json baseline.json

# Run against baseline with regression threshold (5%)
llmsweep run --plain --models qwen2.5-coder-7b-instruct --baseline baseline.json
```

If throughput or TTFT degrades by more than 5% on scenario means:
- The terminal flags the metric with `▼ REGRESSION (+X.X%)`.
- `llmsweep` exits with code `3` (allowing CI/CD assertions to fail on regressions while distinguishing from runtime crashes).

---

## Configuration

Configuration values are resolved using the following order of precedence (highest to lowest):

1. **CLI Arguments**: (e.g. `--models`, `--repeat`)
2. **Environment Variables**:
   - `LLMSWEEP_HOST`: LM Studio host (default: `http://localhost:1234`)
   - `LLMSWEEP_API_KEY`: API key if authentication is enabled (automatically redacted in logs)
   - `LLMSWEEP_TIMEOUT`: Default request timeout in seconds
   - `LLMSWEEP_DATA_DIR`: Directory for storing run artifacts and transcripts
3. **Project Config File**: `llmsweep.toml` or `pyproject.toml` in the current working directory.
4. **User Config File**: `~/.config/llmsweep/config.toml` (or platform equivalent).

---

## Exit Codes

`llmsweep` provides standard exit codes for automated test pipelines:

| Exit Code | Meaning | Description |
| :---: | :--- | :--- |
| **`0`** | Success | All benchmarks completed; no performance regressions. |
| **`1`** | Model Error | One or more models encountered an unrecoverable execution or generation error. |
| **`2`** | Configuration / Setup Error | Invalid arguments, unsupported provider requested, or invalid configuration. |
| **`3`** | Regression Failure | Benchmarks executed successfully, but performance fell below baseline thresholds. |
| **`4`** | Authentication Failure | Non-retryable authentication or authorization error connecting to the provider. |

---

## Development & Testing

All tests in `llmsweep` are fully self-contained and run offline without network access. An autouse socket guard rejects any unintended network calls during test execution.

```bash
# Run unit and contract tests
pytest

# Run linter checks
ruff check

# Run strict type checking
mypy --strict

# Format code
ruff format
```

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
