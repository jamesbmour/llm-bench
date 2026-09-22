---
title: "Overview"
description: "Automated benchmarking and evaluation suite for local LM Studio models"
---

# llmsweep Documentation

Welcome to the documentation for **`llmsweep`**, an automated benchmarking and evaluation suite tailored for local Large Language Models (LLMs) hosted on [LM Studio](https://lmstudio.ai/).

`llmsweep` evaluates models across multi-step tool calling, agentic codebase exploration and bug-fixing, and algorithmic code generation. It tracks performance using strict client-observed metrics (Time to First Token, throughput in tokens/sec, and model loading latency) and multi-run statistical rollups (mean, median, p95). Built-in baseline regression gating enables automated CI/CD testing.

---

## Quick Start

### Prerequisites

| Requirement | Minimum Version |
|---|---|
| Python | 3.11+ |
| Operating System | macOS or Linux |
| LM Studio | Running locally with developer server enabled (default: `http://localhost:1234`) |

### Installation

```bash
git clone https://github.com/jamesbmour/llm-bench.git
cd llm-bench

# Install package and dependencies
uv sync --extra dev   # includes pytest, ruff, mypy, textual-dev

# Verify installation
uv run llmsweep doctor
```

### First Benchmark (Interactive TUI)

```bash
uv run llmsweep run
```

This opens the interactive model picker. Use `Space` to select models, then `Ctrl+R` to start benchmarking. See [CLI Reference → Interactive Terminal UI](cli_reference.md#interactive-terminal-ui-textual) for all keyboard shortcuts.

### First Benchmark (Headless / CI)

```bash
uv run llmsweep run --plain --models "qwen2.5-coder-7b-instruct" --repeat 3
```

For automated regression testing against a baseline:

```bash
uv run llmsweep run --plain \
  --models "qwen2.5-coder-7b-instruct" \
  --baseline baselines/qwen_v1.json \
  --fail-on-regression \
  --json artifacts/latest_run.json
```

If throughput or TTFT degrades by more than 5%, `llmsweep` exits with code **3**.

---

## Documentation Roadmap

<CardGroup cols={2}>
  <Card title="System Architecture" icon="sitemap" href="/architecture">
    Modular design, asynchronous runner, stream parsing, and decoupled renderers.
  </Card>
  <Card title="Benchmark Scenarios" icon="vial" href="/scenarios">
    Specifications for weather, agent-code, and codegen test workloads with tool schemas.
  </Card>
  <Card title="Metrics & Scoring" icon="chart-line" href="/metrics_and_scoring">
    Client-observed TTFT, generation throughput, token fallback, and regression thresholds.
  </Card>
  <Card title="Data Formats & Exports" icon="database" href="/data_formats">
    Run store schema (run.json), transcript files, index.json, and JSON/CSV/Markdown exports.
  </Card>
  <Card title="CLI Reference" icon="terminal" href="/cli_reference">
    Command syntax, model selection, TUI shortcuts, configuration precedence, and exit codes.
  </Card>
  <Card title="Design Rationale" icon="lightbulb" href="/design_rationale_and_assumptions">
    Architectural trade-offs, v1/v0 API discovery, process confinement, and lifecycle rules.
  </Card>
</CardGroup>

```mermaid
flowchart TD
    Index["Overview & Quick Start (/index)"]
    Arch["System Architecture (/architecture)"]
    Scen["Benchmark Scenarios (/scenarios)"]
    Metrics["Metrics & Scoring Methodology (/metrics_and_scoring)"]
    DataFmt["Data Formats & Exports (/data_formats)"]
    CLI["CLI Reference & Configuration (/cli_reference)"]
    Rationale["Design Decisions (/design_rationale_and_assumptions)"]

    Index --> Arch
    Index --> Scen
    Index --> Metrics
    Index --> DataFmt
    Index --> CLI
    Index --> Rationale
```

- [System Architecture](/architecture): Deep dive into the modular design, asynchronous runner, stream parsing, provider adapters, decoupled rendering (Textual TUI vs Plain CLI), and atomic persistence store.
- [Benchmark Scenarios](/scenarios): Comprehensive specifications for the `weather`, `agent-code`, and `codegen` benchmark scenarios, including tool schemas, workspace file contents, pass conditions, and isolation guarantees.
- [Metrics & Scoring Methodology](/metrics_and_scoring): How TTFT, client-observed throughput, token estimation fallbacks, statistical aggregations (mean/median/p95), and baseline regression thresholds are defined and calculated.
- [Data Formats & Exports](/data_formats): Complete schema reference for `run.json`, transcript files, `index.json` registry, and JSON/CSV/Markdown export formats — essential for programmatic analysis of benchmark results.
- [CLI Reference & Configuration](/cli_reference): Command documentation (`run`, `list`, `show`, `export`, `doctor`, `providers`), model selection syntax, TUI keyboard shortcuts, environment variables, configuration precedence, and exit codes.
- [Design Rationale & Assumptions](/design_rationale_and_assumptions): Rationale behind LM Studio v1/v0 API discovery, process confinement vs container sandboxing, serial execution defaults, and lifecycle management.

---

## Key Highlights

1. **Client-Observed Measurement Fidelity**:
   - Clock timing starts at socket dispatch and measures TTFT strictly up to the first meaningful token delta (content, reasoning, or tool call arguments).
   - Throughput is calculated over the active streaming generation window, excluding connection negotiation, model warm-up, and tool execution overhead.
2. **Deterministic & Isolated Execution**:
   - Scenarios execute within sanitized subprocesses with bounded memory, timeout enforcement, and process-group cleanup.
   - Code changes during the `agent-code` scenario are verified with an independent test suite rerun outside the LLM's control.
3. **Decoupled Architecture**:
   - Pure data structures and streaming parsers (`streams.py`, `metrics.py`) have zero I/O side-effects.
   - The central Runner emits uniform lifecycle and stream events consumed identically by the Plain CLI renderer and the interactive Textual TUI.
4. **Idempotent Lifecycle Handling**:
- Automatically loads required models, monitors health and readiness, primes execution with warmup turns, and unloads run-instantiated models while preserving preloaded user models.
5. **Offline-First Storage**:
   - Schema-versioned JSON runs and full conversation transcripts written atomically to disk under the platform data directory. View or export (`json`, `csv`, `markdown`) completely offline via `llmsweep show` and `llmsweep export`. See [Data Formats & Exports](/data_formats) for the complete schema reference.