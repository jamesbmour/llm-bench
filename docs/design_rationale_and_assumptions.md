---
title: "Design Rationale & Assumptions"
description: "Architectural trade-offs, v1/v0 API discovery, process confinement, and lifecycle management"
---

# Design Rationale & Resolved Assumptions

This document explains the architectural decisions, design trade-offs, and resolved assumptions underlying `llmsweep`.

---

## 1. Provider Integration & Discovery

### LM Studio v1 with v0 Fallback
- **Rationale**: LM Studio introduced a modernized v1 REST API (`/v1/models`, `/v1/chat/completions`) with richer capability discovery, structured instance tracking, and explicit load timing. However, older deployments or alternative configurations may only expose the v0 endpoints.
- **Decision**: Discovery queries `/v1/models` first. If and only if the server returns HTTP 404 (`UnsupportedEndpointError`), the provider falls back to the v0 endpoint. Fallback is **not** triggered on connection errors, authentication failures, or 5xx server errors.
- **Reporting Honesty**: In v0, models may be loaded just-in-time (JIT) by the server during request dispatch. Because v0 lacks instance tracking and explicit load readiness APIs, `load_s` is recorded as `null` with status `untracked/v0` rather than publishing guessed metrics.

### Lifecycle Management & Idempotent Cleanup
- **Preserving User State**: Users frequently have a primary model loaded in LM Studio when initiating tests. `llmsweep` captures a snapshot of currently loaded instances at startup.
- **Run-Owned Instances**: When `llmsweep` loads a model, it records the returned runtime instance ID. At session completion or upon `Ctrl+C` interruption, the cleaner attempts to unload **only** instances instantiated during that specific run.
- **Preloaded Models**: Models present prior to run initiation are retained in memory; their `load_s` is reported as `null` with status `preloaded`.

---

## 2. Measurement Methodology

### Client-Observed vs Server-Reported Throughput
- **Rationale**: Server-side metrics often compute throughput using raw internal forward-pass durations, ignoring serialization, buffer queuing, and streaming overhead.
- **Decision**: `llmsweep` measures throughput strictly from the client's perspective: total completed tokens divided by the duration from the first output delta to the final output delta. This reflects real-world application responsiveness.

### Mean of Scenario Means
- **Rationale**: Different scenarios produce widely varying token volumes. For instance, `weather` may generate 40 tokens per turn across 3 turns, whereas `agent-code` may generate hundreds of tokens across multi-file edits.
- **Decision**: If all tokens were pooled globally, `agent-code` would disproportionately dictate the model's throughput score. Averaging scenario means guarantees each benchmark domain carries equal weight in the final model ranking.

### Token Counting Fallback
- **Rationale**: Not all local models or engine configurations return the final `usage` chunk in streaming mode.
- **Decision**: When usage packets are present, `completion_tokens` (including reasoning tokens) is used directly (`token_source: "usage"`). When missing, `llmsweep` falls back to chunk-independent estimation: $\lceil \text{bytes} / 4 \rceil$ (`token_source: "estimated"`). The source is permanently recorded to ensure transparent comparisons.

---

## 3. Sandboxing & Confinement

### Workflow Confinement vs OS Virtualization
- **Rationale**: Running untrusted, LLM-generated code presents security risks. However, requiring Docker, Podman, or virtual machines adds friction and platform-specific dependencies for local testing.
- **Decision**: `llmsweep` enforces **in-process workflow confinement** combined with **sanitized subprocess execution**:
  - `agent-code` tools enforce directory path traversal checks (`..`, absolute paths, symlink escapes) and restrict modifications to permitted code files.
  - Subprocess execution strips ambient `PYTHONPATH`, enforces execution timeouts (15.0s for code generation), and isolates process groups to allow clean SIGKILL termination.
  - Users requiring hostile-code protection should execute `llmsweep` inside containerized or virtualized host environments.

### Independent Test Verification
- **Rationale**: Asking an LLM to evaluate its own code changes or parse test output introduces bias and error.
- **Decision**: In `agent-code`, the model performs file writes. Once complete, an independent test runner runs the test suite in a clean subprocess. The scenario score depends solely on the exit status of this independent test run.

---

## 4. UI Decoupling & Concurrency

### Pure Event-Driven Separation
- **Rationale**: Benchmarking tools that mix UI updates with timing calculations introduce measurement jitter and render pipeline stutter.
- **Decision**: The `Runner` executes benchmark logic independently and emits normalized events over an asynchronous channel. Neither the Plain CLI renderer nor the Textual TUI performs calculations; they act purely as event consumers.

### Stream Buffering
- **Rationale**: Local inference engines can emit hundreds or thousands of deltas per second on high-end hardware (e.g. Apple Silicon Unified Memory or discrete GPUs). Direct UI widget updates on every delta saturate the event loop.
- **Decision**: Output streaming chunks in the TUI are queued and flushed at 75 ms intervals. This maintains smooth, 60 FPS terminal rendering without dropping data or delaying stream reception.

### Serial vs Parallel Execution
- **Rationale**: Running multiple local LLM benchmarks simultaneously causes GPU memory contention, context thrashing, and inaccurate latency measurements.
- **Decision**: Serial execution is the default. Parallel execution is permitted via `--parallel` only when the user explicitly enables it and all target models are preloaded.
