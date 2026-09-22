# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`llmsweep` benchmarks local LM Studio models across three scenarios (`weather` tool-calling, `agent-code` agentic bug-fixing, `codegen` zero-shot implementation), measuring client-observed throughput, TTFT, load latency, and task correctness. Python 3.11+, macOS/Linux, `src/` layout, hatchling, entry point `llmsweep = "llmsweep.cli:main"`.

**The package is mid-build.** `README.md` and `Implementation_plan.md` describe the finished v1; only milestone 1 (pure logic: `streams.py`, `models.py`, `selection.py`, `metrics.py`, `errors.py`) exists so far. Treat the README as a spec, not a description of current behavior — `llmsweep.cli` does not exist yet, so the console script does not run.

## Commands

Dependencies are managed with `uv` (`uv.lock` is committed). Prefix with `uv run`, or activate `.venv`.

```bash
uv sync --extra dev              # install dev environment
uv run pytest                    # all tests (offline, fast)
uv run pytest tests/test_streams.py::test_ndjson_yields_nothing   # single test
uv run ruff check                # lint
uv run ruff format               # format
uv run mypy --strict             # strict type check (config already sets files/strict)
```

**Release gate for every change:** `pytest`, `ruff check`, and `mypy --strict` must all pass. `Implementation_plan.md` requires each milestone to clear all three before the next begins. `pyproject.toml` sets `filterwarnings = ["error"]` and `--strict-markers`, so warnings and unregistered markers are failures.

## Architecture

### Two trees — only one is the package

- `src/llmsweep/` — the real package. Everything new goes here.
- `llmsweep/lmstudio_agent_bench.py` — a 1,966-line **reference script**, kept as LM Studio integration and scoring guidance only. It is excluded from the wheel, from `ruff` (`extend-exclude`), and from `mypy` (`exclude`). Do not refactor it, do not import from it, and do not treat its result JSON as a historical llmsweep schema. Read it when you need the exact scoring rules, tool schemas, or LM Studio endpoint behavior to reimplement.

### Layering

Milestone 1 modules are **pure and I/O-free** — no `httpx`, no filesystem, no clock reads except values passed in. Keep them that way; provider/runner/store work belongs in new modules.

- `errors.py` — the typed error taxonomy every other module raises from. Errors are grouped by *category so callers act on type, not on string matching*: `ConfigError` (→ exit 2), `TransportError`/`HttpStatusError`/`StreamError` under `ProviderError`, `ModelError` lifecycle, `StoreError`. Each carries `retryable`; `HttpStatusError.retryable` is derived from the status (≥500). `AuthenticationError` → exit 4. Add new failure modes here rather than raising built-ins.
- `streams.py` — two stacked layers. `SseDecoder` does resumable byte-level SSE framing that tolerates chunk boundaries anywhere (mid-line, mid-CRLF, mid-UTF-8); `ChatStreamParser` normalizes OpenAI-shaped chunks into `StreamEvent` (`TextDelta | ReasoningDelta | ToolCallDelta | UsageEvent | FinishEvent`). `ToolCallAccumulator` reassembles indexed tool fragments and **keeps `arguments` as the raw string** until the whole call is complete, so malformed arguments can be reported faithfully.
- `models.py` — normalizes LM Studio metadata into `ModelInfo`. Identity is `ModelRef(provider, id)` everywhere. Handles v1 (`key`) vs v0 (`id`) identifiers and both dict and list `capabilities` shapes.
- `selection.py` — shared by the CLI and the TUI picker. Resolution order: exact ID → index/range → unique substring; preserves order, deduplicates, and raises `SelectionError` on ambiguity.
- `metrics.py` — `TurnRecorder.observe()` consumes stream events plus a timestamp; `finish()` produces `TurnMetrics`. Aggregation via `summarize()` (mean/median/nearest-rank p95), `pooled_throughput()`, `regression_pct()`.

### Measurement invariants

These are the point of the project — preserve them exactly when touching `metrics.py` or `streams.py`:

- **`is_output_delta()` is what keeps TTFT honest.** Only `TextDelta`, `ReasoningDelta`, and `ToolCallDelta` count as output. Role-only envelopes produce no event at all; `UsageEvent` and `FinishEvent` are explicitly excluded.
- **TTFT** = request dispatch → first output delta. **Generation window** = first output delta → last output delta.
- **Throughput is client-observed**: `output_tokens / generation_window_seconds`, excluding load, warmup, tool execution, and checker time. A zero-length window yields `None`, not zero.
- **Token accounting**: prefer API `completion_tokens` (`token_source="usage"`); otherwise the chunk-independent estimate `ceil(utf8_output_bytes / 4)` (`token_source="estimated"`). `token_source` must survive into results and exports — mixed sources invalidate baseline comparison.
- Model throughput is the **mean of scenario means**, not a global pool. Scenario samples pool across turns.
- Malformed SSE raises `StreamProtocolError` rather than being silently skipped. An NDJSON body fed to the SSE decoder must yield **zero** events (Ollama-style output must not be misread as deltas).

### Model lifecycle rules (for the provider milestone)

Discover via v1, fall back to v0 **only** on `UnsupportedEndpointError` — never on auth, malformed responses, or transient failures. Snapshot loaded instances first; unload only instances this run created, identified by the returned instance ID, then verify disappearance. Never substitute a model name for an unknown instance ID. Preloaded models stay untouched and report `load_s: null`.

### Exit codes

`0` success · `1` model error · `2` config/setup · `3` regression only · `4` non-retryable auth. A scoring failure (wrong answer) is **not** a model error — keep those distinct.

## Testing conventions

`tests/conftest.py` installs an **autouse socket guard** that monkeypatches `socket.connect`, `connect_ex`, and `getaddrinfo` to raise. All tests are offline; provider tests use fixtures/stubs, never a live LM Studio. `asyncio_mode = "auto"`, so async tests need no decorator.

Byte-level fragmentation is tested by feeding the stream one byte at a time (`test_streams.py`) — keep that pattern for new stream fixtures.

## Diagrams

`.github/instructions/mermaid.instructions.md` governs diagram work: write diagrams to `.mmd` files, validate syntax before presenting, and don't hand back unvalidated Mermaid. The VS Code extension tools it names are unavailable from the CLI — validate by inspection instead of claiming a tool ran.
