---
title: "Data Formats & Exports"
description: "Run store schema, transcript files, index.json format, and JSON/CSV/Markdown export structures"
---

# Data Formats & Exports

`llmsweep` persists benchmark results as canonical, schema-versioned JSON documents. This page documents the on-disk run store layout, the `run.json` document structure, individual transcript files, the `index.json` registry, and all three offline export formats (JSON, CSV, Markdown). Understanding these structures is essential for programmatic analysis of benchmark results or building downstream tooling.

---

## Run Store Layout

All persistent data lives under a platform-specific directory:

| Platform | Default Path |
|---|---|
| Linux | `~/.local/share/llmsweep` |
| macOS | `~/Library/Application Support/llmsweep` |

Override with `--run-store DIR` or `LLMSWEEP_RUN_STORE`. The layout is:

```text
<run-store>/
  index.json                          # Run registry (append-only, fs-locked)
  .index.lock                         # Advisory lock for concurrent writes
  runs/
    <UTC-timestamp>-<8-hex-suffix>/   # One directory per run
      run.json                        # Canonical run document
      transcripts/                    # Per-sample conversation logs
        lmstudio_<model-slug>_<16-char-hash>_<scenario>_r<N>.json
```

When `--transcript-dir DIR` is set, an additional copy of each transcript is written under `DIR/<run-id>/`.

---

## run.json Schema (Schema Version 1)

The canonical run document. All numeric values are JSON numbers; all timestamps are ISO-8601 UTC strings. Fields marked *derived* appear in the persisted document but are recomputed from raw data on load — they are stripped during deserialization to avoid stale state.

### Top-Level Structure

```json
{
  "run_id": "20260922T143000000Z-a1b2c3d4",
  "started_at": "2026-09-22T14:30:00.000000+00:00",
  "settings": { ... },
  "models": [ ... ],
  "status": "completed",
  "schema_version": 1,
  "comparisons": [ ... ]
}
```

| Field | Type | Description |
|---|---|---|
| `run_id` | string | UTC timestamp + 8-char hex suffix; matches `[A-Za-z0-9_-]+`. |
| `started_at` | string | ISO-8601 UTC start time. |
| `settings` | object | Frozen copy of the resolved `RunOptions.settings()` dict (see below). |
| `models` | array | List of model result objects (see [Model Result](#model-result)). |
| `status` | string | `"running"`, `"completed"`, `"cancelled"`, or `"error"`. |
| `schema_version` | integer | Always `1`. Future versions require explicit migration. |
| `comparisons` | array | Baseline comparison verdicts (empty if no `--baseline`). See [Comparisons](#comparisons). |

### Settings Object

A JSON-normalized copy of the frozen `RunOptions` dataclass:

```json
{
  "scenarios": ["weather", "agent-code", "codegen"],
  "repeat": 1,
  "max_tokens": 1024,
  "max_turns": null,
  "load_timeout": 600.0,
  "no_warmup": false,
  "keep_loaded": false,
  "parallel": 1,
  "task": null,
  "benchmark_version": 1
}
```

These settings are used to determine baseline comparability — runs with mismatched `scenarios`, `max_tokens`, `max_turns`, `task`, `no_warmup`, or `benchmark_version` cannot be compared.

### Model Result

Each entry in the `models` array:

```json
{
  "model": { ... },       // ModelInfo (see below)
  "status": "completed",  // pending | running | completed | error | cancelled
  "load_s": 2.34,         // null if preloaded or v0 JIT
  "load_status": "loaded by this run",
  "warmup_s": 0.15,       // null if --no-warmup
  "samples": [ ... ],     // Per-scenario-per-repeat results (see below)
  "total_s": 42.78,       // Wall-clock duration of the entire model run
  "error": null,          // Redacted error message if status == "error"
  "error_code": 0,        // Exit code mapping: 1=generic, 2=config, 4=auth
  "warnings": [],         // e.g. ["tool capability unknown; attempting requested scenarios"]
  "contended": false,     // true when --parallel > 1 (excluded from regression verdicts)

  // --- Derived fields (recomputed on load) ---
  "provider": "lmstudio",
  "id": "qwen2.5-coder-7b-instruct",
  "tok_s": null,          // mean of scenario means; null if any scenario has no valid throughput
  "ttft_ms": 142.3,       // mean TTFT across completed samples
  "token_source": "estimated"
                        // "estimated" if any turn used estimation; else the single source
  "token_sources": ["usage"],
  "output_tokens": 847,   // sum of output tokens across all turns
  "total_tokens": null,   // null if any turn lacks server-reported total_tokens
  "reasoning_tokens": null,
  "success_rate": 1.0,    // fraction of scored samples that passed (null if none scored)
  "success": true,        // all scored samples passed (null if none scored)
  "turns": 24,            // total turn count across all samples
  "tools_called": ["get_weather", "convert_temperature"],
  "expected_tools": ["get_weather", "convert_temperature", "get_current_time"],
  "tool_errors": [],      // list of tool error strings from failed calls
  "cost": null,           // always null in LM Studio v1 (not billed)
  "serving_provider": null,
  "scenarios": { ... }    // Per-scenario rollups (see below)
}
```

### ModelInfo Object

Embedded within each model result:

| Field | Type | Description |
|---|---|---|
| `ref` | object | `{"provider": "lmstudio", "id": "..."}` — globally unique identity. |
| `type` | string | `"llm"`, `"embedding"`, `"embeddings"`, `"reranker"`, or `"unknown"`. Non-chat types are excluded from benchmarks. |
| `params_b` | float \| null | Parameter count in billions, parsed from `params_string`. Falls back to parsing the model ID. |
| `format` | string \| null | Quantization format (e.g. `"gguf"`). |
| `quantization` | string \| null | Quantization level (e.g. `"Q4_K_M"`). |
| `tool_use` | bool \| null | `true`/`false` from capabilities, or `null` if unknown. Unknown capability is attempted unless `--require-tool-use`. |
| `vision` | bool \| null | Vision support flag. |
| `instances` | array[string] | Loaded instance IDs at discovery time (v1 only). Empty for v0. |
| `loaded` | boolean | Whether the model was loaded when discovered. |

### Sample Result

Each entry in a model's `samples` array:

```json
{
  "scenario": "weather",
  "repeat": 1,
  "status": "completed",   // running | completed | skipped | error | cancelled
  "success": true,         // null for custom tasks (--task) or unscored scenarios
  "answer": "...",         // Concatenated text deltas (final model response)
  "output": "all three tools and 64.4 F required",
  "turns": [ ... ],        // Per-turn TurnMetrics (see below)
  "tools_called": ["get_weather", "convert_temperature"],
  "expected_tools": ["get_weather", "convert_temperature", "get_current_time"],
  "tool_errors": [],       // Error strings from tool calls that returned {"error": ...}
  "messages": [ ... ],     // Full conversation history (see Transcript Files)
  "total_s": 3.42,         // Wall-clock duration of this sample
  "error": null,
  "transcript": "transcripts/lmstudio_qwen-..._weather_r1.json",

  // --- Derived fields (recomputed on load) ---
  "tok_s": 85.3,          // pooled throughput: sum(output_tokens) / sum(generation_s); n/a if any turn has zero generation time
  "ttft_ms": 142.3,       // mean TTFT across turns in this sample
  "output_tokens": 290,   // sum of output tokens across all turns
  "token_sources": ["usage"],
  "token_source": "usage"
}
```

### TurnMetrics Object

Each entry in a sample's `turns` array:

| Field | Type | Description |
|---|---|---|
| `ttft_ms` | float \| null | Time from dispatch to first output delta, in milliseconds. Null if no output was produced. |
| `generation_s` | float | Duration from first to last output delta (the streaming window). Always ≥ 0; may be 0 for single-chunk returns. |
| `output_tokens` | integer | Token count: API-reported `completion_tokens` when available, otherwise $\lceil \text{bytes} / 4 \rceil$. |
| `token_source` | string | `"usage"` (API-reported) or `"estimated"` (byte-based fallback). The value `"server"` exists in the type definition but is never produced by current code. |
| `total_tokens` | integer \| null | API-reported total tokens, if available. Null when usage packets are absent. |
| `reasoning_tokens` | integer \| null | Internal reasoning/thinking token count from `completion_tokens_details`, if reported. |

### Per-Scenario Rollups

The derived `scenarios` object in each model result contains per-scenario statistical summaries:

```json
{
  "weather": {
    "tok_s": {"count": 1, "mean": 85.3, "median": 85.3, "p95": 85.3},
    "ttft_ms": {"count": 1, "mean": 142.3, "median": 142.3, "p95": 142.3},
    "total_s": {"count": 1, "mean": 3.42, "median": 3.42, "p95": 3.42},
    "success_rate": 1.0,
    "token_sources": ["usage"],
    "complete": true
  }
}
```

Each metric uses the `Stats` structure: `{count, mean, median, p95}`. The nearest-rank p95 is computed as $X_{\lceil 0.95 \times K \rceil - 1}$ (0-indexed) over sorted valid observations of length $K$. With a single sample ($K=1$), p95 equals that value; with no valid observations, all statistics are `null`.

### Comparisons

When `--baseline` is provided, each model/scenario pair gets a comparison record:

```json
{
  "model": "lmstudio:qwen2.5-coder-7b-instruct",
  "scenario": "weather",
  "verdict": "within threshold",   // regression | within threshold | not comparable
  "reason": null,                  // Explanation when verdict is "not comparable"
  "tok_s_regression_pct": -3.2,    // Negative = improvement; positive = degradation
  "ttft_regression_pct": 1.8       // Positive = slower (regression); negative = faster
}
```

**Verdict logic:**
- `regression`: tok/s dropped by more than the threshold OR TTFT increased by more than the threshold.
- `within threshold`: Both metrics are within acceptable variance.
- `not comparable`: Baseline missing, settings differ, contended measurements, incomplete data, or mismatched token sources.

---

## Transcript Files

Each sample produces a transcript file containing the full conversation history:

```json
{
  "provider": "lmstudio",
  "model": "qwen2.5-coder-7b-instruct",
  "scenario": "weather",
  "repeat": 1,
  "messages": [
    {"role": "user", "content": "Get the weather in Paris..."},
    {"role": "assistant", "content": "...", "reasoning_content": "...", "tool_calls": [...]},
    {"role": "tool", "tool_call_id": "call_abc123", "content": "{\"city\": \"Paris\", ...}"},
    ...
  ],
  "answer": "...",   // Final assistant text response
  "output": "...",   // Scoring output message
  "error": null,
  "status": "completed"
}
```

**Filename convention:** `transcripts/<provider>_<model-slug>-<16-char-sha256-of-ref-key>_<scenario>_r<repeat>.json`

The identity hash prevents sanitized filenames from colliding when model IDs differ only in characters that get replaced by underscores.

---

## Export Formats

### JSON Export (`--json PATH`)

A complete copy of the `run.json` document, with all credentials redacted via the provider's `Redactor`. This is identical to what you'd find on disk — see [run.json Schema](#runjsonschema-schema-version-1) above.

```bash
llmsweep export /path/to/run --json report.json
```

### CSV Export (`--csv PATH`)

One row per sample (scenario repeat). Columns:

| Column | Description |
|---|---|
| `provider` | Provider name (e.g. `lmstudio`). |
| `model` | Model ID without provider prefix. |
| `scenario` | Scenario name (`weather`, `agent-code`, `codegen`). |
| `repeat` | 1-based repeat index. |
| `status` | Sample status. |
| `success` | Boolean or empty (null). |
| `tok_s` | Pooled throughput for this sample, or empty if n/a. |
| `ttft_ms` | Mean TTFT across turns in this sample. |
| `token_source` | Comma-separated list of token sources used (`usage`, `estimated`). |
| `load_s` | Model load time (same value repeated per sample). |
| `warmup_s` | Warmup turn duration. |
| `total_s` | Sample wall-clock duration. |
| `output_tokens` | Sum of output tokens across all turns. |
| `total_tokens` | Sum of total tokens, or empty if any turn lacks server data. |
| `reasoning_tokens` | Sum of reasoning tokens, or empty if unavailable. |
| `turns` | Number of conversation turns in this sample. |
| `tools_called` | Comma-separated list of tool names invoked. |
| `expected_tools` | Comma-separated list of required tool names for scoring. |
| `tool_errors` | Semicolon-separated error strings from failed tool calls. |
| `contended` | Boolean: true if measured under parallel contention. |
| `error` | Redacted error message, or empty. |

### Markdown Export (`--markdown PATH`)

A human-readable report with a summary table per provider and an embedded plain-text rendering of the full run output:

```markdown
# llmsweep 20260922T143000000Z-a1b2c3d4

Client-observed tok/s; mean of scenario means.

## lmstudio

| Model | Status | Success rate | tok/s | TTFT ms | Source | Error |
|---|---|---:|---:|---:|---|---|
| qwen2.5-coder-7b-instruct | ✔ completed | 1.0 | 85.3 | 142.3 | usage |  |

Speed comparisons are valid only within a provider and matching token sources.

```text
Run ... | completed
...full plain-text table output...
```
```

---

## Offline Inspection Commands

### `llmsweep show PATH`

Opens a run directory or `run.json` file without connecting to LM Studio:

```bash
# Open by run directory (reads runs/<id>/run.json)
llmsweep show ~/.local/share/llmsweep/runs/20260922T143000000Z-a1b2c3d4

# Open a specific JSON file directly
llmsweep show /path/to/run.json --plain  # ANSI-free tabular output
```

### `llmsweep export PATH`

Exports an offline run to one or more formats:

```bash
llmsweep export ~/.local/share/llmsweep/runs/<run-id> \
  --json report.json --csv report.csv --markdown report.md
```

All exports apply credential redaction. The `--sort-by` flag controls model ordering in CSV and Markdown output (`order`, `tok_s`, `ttft`, `total`, `load`, `model`).

---

For the full CLI command reference including flags, environment variables, and configuration precedence, see [CLI Reference & Configuration](cli_reference.md). For details on how metrics are calculated from this data, see [Metrics & Scoring Methodology](metrics_and_scoring.md).