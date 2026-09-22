---
title: "Overview"
description: "Automated benchmarking and evaluation suite for local LM Studio models"
---

# llmsweep Documentation

Welcome to the documentation for **`llmsweep`**, an automated benchmarking and evaluation suite tailored for local Large Language Models (LLMs) hosted on [LM Studio](https://lmstudio.ai/).

`llmsweep` evaluates models across multi-step tool calling, agentic codebase exploration and bug-fixing, and algorithmic code generation. It tracks performance using strict client-observed metrics (Time to First Token, throughput in tokens/sec, and model loading latency) and multi-run statistical rollups (mean, median, p95).

---

## Documentation Roadmap

<CardGroup cols={2}>
  <Card title="System Architecture" icon="sitemap" href="/architecture">
    Modular design, asynchronous runner, stream parsing, and decoupled renderers.
  </Card>
  <Card title="Benchmark Scenarios" icon="vial" href="/scenarios">
    Specifications for weather, agent-code, and codegen test workloads.
  </Card>
  <Card title="Metrics & Scoring" icon="chart-line" href="/metrics_and_scoring">
    Client-observed TTFT, generation throughput, token fallback, and regression thresholds.
  </Card>
  <Card title="CLI Reference" icon="terminal" href="/cli_reference">
    Command syntax, selection flags, configuration precedence, and exit codes.
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
    Metrics["Metrics & Methodology (/metrics_and_scoring)"]
    CLI["CLI & Configuration (/cli_reference)"]
    Rationale["Design Decisions (/design_rationale_and_assumptions)"]

    Index --> Arch
    Index --> Scen
    Index --> Metrics
    Index --> CLI
    Index --> Rationale
```

- [System Architecture](/architecture): Deep dive into the modular design, asynchronous runner, stream parsing, provider adapters, decoupled rendering (Textual TUI vs Plain CLI), and atomic persistence store.
- [Benchmark Scenarios](/scenarios): Comprehensive specifications for the `weather`, `agent-code`, and `codegen` benchmark scenarios, including sandbox confinement, tooling interfaces, and scoring criteria.
- [Metrics & Scoring Methodology](/metrics_and_scoring): How TTFT, client-observed throughput, token estimation fallbacks, statistical aggregations, and baseline regression thresholds are defined and calculated.
- [CLI Reference & Configuration](/cli_reference): Command documentation (`run`, `list`, `show`, `export`, `doctor`, `providers`), model/scenario selection syntax, environment variables, configuration precedence, and exit codes.
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
