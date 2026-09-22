# Repository Guidelines

`llmsweep` benchmarks agentic, coding, and throughput performance of local LM Studio models. It is a Python 3.11+ CLI (plain, ANSI-free, pipeable) plus a Textual TUI (interactive). macOS and Linux only. `Implementation_plan.md` is the authoritative spec; `README.md` is the user-facing doc.

## Project Overview

Three scenarios run per model by default — `weather` (multi-step tool calling), `agent-code` (inspect workspace, fix a bug, verify), `codegen` (write `fib()` from spec, no tools). Execution is serial by default, one load + warmup per model, then all repeats.

Measurement contract (implemented, do not dilute):

- **TTFT** = request dispatch → first content/reasoning/tool-call delta. Role-only headers and usage packets are excluded (`is_output_delta`).
- **Throughput** = `output_tokens / generation_window_s`, window = first output delta → last output delta. Client-observed; excludes load, warmup, tool execution, checker time. Zero-duration window → `None` (`n/a`).
- **Token accounting** prefers API `completion_tokens` (including reasoning); else `ceil(utf8_output_bytes / 4)` — chunk-independent. `token_source` is preserved on every sample.
- Rollups: mean / median / nearest-rank p95. Model throughput is the **mean of scenario means**.
- Regression thresholds default 5% on scenario means for TTFT and throughput.

## Architecture & Data Flow

Strictly layered and acyclic. The core is pure, sync, and I/O-free; the provider is async and injects all I/O.

```
errors      (leaf)
security    (leaf)

models      ──► errors
streams     ──► errors
metrics     ──► errors, streams
selection   ──► errors, models

providers/base       ──► models, security, streams
providers/lmstudio   ──► errors, models, security, streams, providers/base
```

Nothing in the pure core imports `providers`. `__init__.py` imports nothing.

Planned (not yet written), in dependency order:

```
scenarios  →  runner/  · results · store  →  render (plain)  →  tui (Textual)
```

Data flow: `LMStudio.chat` streams HTTP bytes → `ChatStreamParser` yields normalized `StreamEvent`s → `TurnRecorder` accumulates turn observations → runner pools turn metrics into scenario samples, rolls up repeats, persists → renderers (plain and TUI) consume the same events and results.

**Invariants:**

- Model identity is the `(provider, id)` tuple (`ModelRef`) end-to-end — selection, storage, comparison. Never a bare name.
- Renderers MUST NOT compute scores or metrics. They render only.
- The runner owns execution, cancellation, measurement, and persistence.
- Completed / failed / skipped / cancelled are distinct outcomes; failed models stay in reports.
- `errors` and `security` are leaves; the pure core (`streams`, `models`, `metrics`, `selection`) MUST NOT import `providers`.
- Only instances this run loaded are ever unloaded, and only by their returned instance ID.

### Provider seam

`providers/base.py` defines the contract the runner will program against:

```python
class Provider(Protocol):
    redact: Redactor

    async def list_models(self) -> list[ModelInfo]: ...
    async def acquire(self, model: ModelInfo, load_deadline: float) -> Lease: ...
    async def release(self, lease: Lease) -> None: ...
    async def close(self) -> None: ...
    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]: ...
```

`Lease` is the ownership record: `model`, `instance_id`, `we_loaded`, `load_s`, `note`, `released`, and a `chat_id` property (`instance_id or model.ref.id`). `we_loaded` is what gates the unload — `release()` is a no-op unless this run loaded the instance, and it is idempotent (`released` flag).

`chat()` is deliberately **not** `async def` — it is a sync function returning an `AsyncIterator`, so callers write `async for event in provider.chat(...)`.

Lifecycle rules, all implemented in `LMStudio`:

- Discover via `/api/v1/models`; fall back to `/api/v0/models` **only** on `UnsupportedEndpointError` — never on auth, malformed responses, or transient failures. The resolved version is cached on `self.version`.
- `acquire` snapshots loaded instances first. Already-loaded → `Lease(..., note="already loaded")` with no load call, `load_s` stays `None`. v0 returns a JIT lease with an explanatory note (v0 has no load timing or unload control).
- Load captures the returned `instance_id`; `ModelLoadError` if the server omits it, because ownership would be unverifiable. Readiness is polled every 1s inside `asyncio.timeout(load_deadline)`; `load_s` prefers the reported `load_time_seconds`, else measured through readiness.
- The load POST is issued with `retry=False` — a timed-out POST may still complete remotely, so it is never blindly repeated.
- `release` unloads, then polls up to 10× for the instance to disappear; `ModelUnloadError` if unconfirmed. A failed unload is verified again before being raised.
- Credentials are redacted on the way out of every error path, including `exc.message` inside the chat worker.

## Key Directories

| Path | Purpose |
| --- | --- |
| `src/llmsweep/` | The installed package (`src` layout) |
| `src/llmsweep/providers/` | `base.py` (`Lease`, `Provider` Protocol), `lmstudio.py` (async httpx adapter) |
| `src/llmsweep/security.py` | `Redactor` — secret scrubbing for logs/errors/exports |
| `src/llmsweep/scenarios/` | **Empty** — milestone 3, weather / agent-code / codegen |
| `src/llmsweep/render/` | **Empty** — milestone 4, plain renderer |
| `src/llmsweep/tui/` | **Empty** — milestone 5, Textual screens |
| `tests/fakes/lmstudio.py` | `FakeLMStudio` + `httpx.MockTransport`; `completion()`, `calls()` script builders |
| `tests/fixtures/{lmstudio,streams}/` | **Empty** — fixtures are built inline by `tests/fakes/lmstudio.py` instead |
| `llmsweep/` | **Legacy reference script only** — NOT the package |
| `docs/` | Design docs — `index`, `architecture`, `scenarios`, `metrics_and_scoring`, `cli_reference`, `design_rationale_and_assumptions`; describe finished v1, treat as spec |
| `.kilo/`, `.codegraph/`, `.ruff_cache/`, `.venv/` | Local tooling, gitignored |

**Footgun:** `llmsweep/lmstudio_agent_bench.py` (1966 lines, `urllib`, `DEFAULT_HOST="localhost"`, `DEFAULT_PORT=1234`) is a repo-root directory that is *not* the `llmsweep` package and is *not* installed. It is excluded from ruff, mypy, and the sdist. Treat it as read-only integration guidance (it uses `/api/v1/models`, `/api/v0/models`, `/api/v1/models/load`, `/api/v1/models/unload`, `/v1/chat/completions`). Never import from it.

## Development Commands

No Makefile, tox, nox, pre-commit, or CI workflows exist. The full local gate:

```bash
uv sync --extra dev          # create/refresh the dev environment
uv run pytest                # unit + contract tests
uv run ruff check            # lint
uv run ruff format           # format
uv run mypy --strict         # type check
```

`uv run` works without activating `.venv`; `.venv/bin/python -m pytest` is equivalent. Single test: `uv run pytest tests/test_streams.py::test_ndjson_yields_nothing`.

Per-milestone definition of done (from the spec): tests + `ruff check` + `mypy --strict`, then the milestone's own acceptance command. Release gate adds package build/install and CLI smoke tests. Once the CLI exists: `llmsweep run --plain`, `list`, `show`, `export`, `doctor`, `providers`.

`mypy --strict` is enforced via `[tool.mypy] strict = true`, not just the CLI flag.

## Milestone Status

`Implementation_plan.md` defines five milestones. **Milestones 1 and 2 are complete.** Each milestone must pass its tests, Ruff, and strict mypy before the next begins.

| # | Scope | State |
| --- | --- | --- |
| 1 | Pure logic: `streams`, `models`, `selection`, `metrics` | Done |
| 2 | LM Studio provider + HTTP stubs | Done — `providers/`, `security.py`, `tests/fakes/lmstudio.py` |
| 3 | Scenarios, runner, results, store | Not started |
| 4 | Plain renderer + full CLI | Not started |
| 5 | Textual TUI + release deliverables | Not started |

**`src/llmsweep/cli.py` does not exist yet**, so the declared console script `llmsweep = "llmsweep.cli:main"` is currently unresolvable. Do not write code that assumes it exists.

`CLAUDE.md` is a sibling agent-instruction file covering the same ground. It has drifted (it still says only milestone 1 exists and that `uv.lock` is committed). Where they disagree, trust this file and the code.

Spec-mandated deliverables still missing: `CHANGELOG.md`, an `export_screenshot()` image, and macOS/Ubuntu CI for 3.11 + current stable. `docs/` is now populated (`index`, `architecture`, `scenarios`, `metrics_and_scoring`, `cli_reference`, `design_rationale_and_assumptions`) — note these describe the *finished* v1, so treat them as spec, not current behavior.

## Documentation Platform (Mintlify)

Documentation is managed via **Mintlify** (`mint` CLI) with site configuration in `docs/docs.json`.

- **Structure**: All docs live under `docs/` with site entrypoint `docs/index.md`.
- **Configuration**: `docs/docs.json` defines site metadata, theme, brand colors, navbar links, and navigation groups.
- **Navigation**: Pages are registered without file extension in `docs/docs.json` under `navigation.groups`.
- **Frontmatter**: Every documentation file must start with YAML frontmatter containing `title` and `description`.
- **Cross-links**: Use site-relative markdown links (`/architecture`, `/scenarios`, `/metrics_and_scoring`, `/cli_reference`, `/design_rationale_and_assumptions`) rather than absolute file system URLs.
- **Preview & Verification**:
  ```bash
  cd docs && mint dev
  # or from project root
  npm run docs:dev
  ```
- **Validation**:
  ```bash
  cd docs && mint validate
  cd docs && mint broken-links
  # or from project root
  npm run docs:validate
  npm run docs:broken-links
  ```
- **Deployment**: Connect the GitHub repository in the Mintlify dashboard (pointing to the `docs` folder) for continuous Git synchronization and automated deployments on push to `main`.

## Code Conventions & Common Patterns

**Headers.** Every module starts with `from __future__ import annotations`. `__init__.py` holds only `from __future__ import annotations` and `__version__`; it re-exports nothing — import from submodules (`from llmsweep.models import ModelRef`).

**Type hints everywhere**, on parameters, returns, and module constants. `mypy --strict` plus `warn_unreachable`; unused-ignore and redundant-bool errors are enabled, so a stale `# type: ignore` fails the build.

**Dataclasses, three policies in use:**

```python
@dataclass(frozen=True, slots=True)   # stream events, SsePacket, ToolCall — hot path
@dataclass(frozen=True, order=True)   # ModelRef, ModelInfo, Stats, TurnMetrics — value types
@dataclass                            # TurnRecorder, ToolCallAccumulator — mutable accumulators
```

Never hand-roll `__eq__`, `__hash__`, or a mutable default. Use `field(default_factory=...)`.

**Stateful decoders** (`SseDecoder`, `ChatStreamParser`) declare explicit `__slots__` and a manual `__init__` — no hidden attribute allocation.

**Docstrings.** Module docstring on every non-trivial module; class docstrings on public classes. `noqa`/`# type: ignore` must carry a reason.

**Naming.** `snake_case` functions/vars, `PascalCase` types, leading `_` for private helpers and constants (`_opt_int`, `_OUTPUT_EVENTS`, `_slots`). Module constants are `UPPER_SNAKE` (`REGISTERED_PROVIDERS`, `NON_CHAT_TYPES`, `INDEX`, `SCENARIOS`, `DONE_SENTINEL`). Public API is listed in `__all__` where a module defines one (`errors.py`, `streams.py`); `models.py`, `selection.py`, `metrics.py` intentionally omit it.

**Error handling.** Raise the typed taxonomy in `errors.py` — never bare `Exception`, never string-matching on messages. The base is `LlmsweepError`, carrying `message: str` and a `retryable: bool` class attribute. `HttpStatusError.retryable` is a property returning `status_code >= 500`. Add a class here rather than a local error type. Retry policy: at most three attempts for eligible connect/5xx failures with jitter; a stream is never restarted after output has been produced.

**Missing values** are uniformly `None` — never `NaN`, never a sentinel string. Aggregates return `None` rather than raising when inputs are absent, which is what surfaces as `n/a` in reports.

**Async.** The pure core stays sync. Async lives in `providers/` (httpx) and, later, the TUI (`@work`) — `asyncio_mode = "auto"` means async tests need no decorator. Never make `streams`/`models`/`metrics`/`selection` async.

**Dependency injection over patching.** Every non-deterministic dependency is a constructor argument with a production default:

```python
LMStudio(base_url=..., api_key=..., timeout=..., *,
         transport: httpx.AsyncBaseTransport | None = None,
         sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
         clock: Callable[[], float] = time.monotonic)
```

Tests pass `transport=fake.transport, sleep=no_wait` — never `monkeypatch` httpx, `time`, or `random`. Follow this shape for new I/O boundaries.

**Byte-safe streaming.** Decoding goes through `codecs.getincrementaldecoder("utf-8")(errors="replace")` — chunk boundaries may fall mid-line, mid-CRLF, or mid-UTF-8 sequence. Never `bytes.decode()` a partial chunk. Tool-call arguments stay raw strings until the full call is assembled; parse only via `ToolCall.parsed_arguments()`.

## Important Files

| File | Role |
| --- | --- |
| `src/llmsweep/errors.py` | Full exception taxonomy; `retryable` flags; exit-code mapping in docstrings |
| `src/llmsweep/streams.py` | `SseDecoder` (resumable framing) + `ChatStreamParser` (normalization); `StreamEvent` union |
| `src/llmsweep/metrics.py` | `TurnRecorder`, `TurnMetrics`, `summarize`, `pooled_throughput`, `regression_pct` |
| `src/llmsweep/models.py` | `ModelRef`, `ModelInfo`, `normalize_model`, `parse_ref`, `sort_models` |
| `src/llmsweep/selection.py` | `resolve_selection`, `resolve_scenarios`, `parse_index_spec`, `filter_models` |
| `src/llmsweep/security.py` | `Redactor` — literal-secret replace plus a `Bearer <token>` regex |
| `src/llmsweep/providers/base.py` | `Lease`, `Provider` Protocol — the runner's contract |
| `src/llmsweep/providers/lmstudio.py` | `LMStudio` async adapter; `sse()` test-helper frame builder |
| `tests/fakes/lmstudio.py` | Socket-free `FakeLMStudio`, script builders `completion()` / `calls()` |
| `src/llmsweep/__init__.py` | `__version__` only |
| `tests/conftest.py` | Autouse `no_network` socket guard |
| `pyproject.toml` | Build, deps, and all tool config inline |
| `Implementation_plan.md` | Authoritative architecture and acceptance spec |
| `README.md` | User-facing CLI/config/exit-code reference |

`selection.resolve_selection` precedence, in order: whole-spec exact `provider:id` ref → per-part exact ref → 1-based index/range spec → case-insensitive unique substring. Ambiguous or out-of-range → `SelectionError`. Input order is preserved and duplicates dropped.

`ChatStreamParser` yields `TextDelta`, `ReasoningDelta`, `ToolCallDelta`, `UsageEvent`, `FinishEvent`. A usage-only packet arrives with an empty `choices` list and still yields a `UsageEvent`. Feeding NDJSON (the v0 shape) into it yields zero events. Malformed payloads raise `StreamProtocolError`.

## Runtime/Tooling Preferences

- **Python ≥ 3.11** (`requires-python` and ruff `target-version = "py311"`); the local venv is CPython 3.12.11 with `uv` 0.8.24.
- **`uv` is the package manager.** `uv.lock` exists on disk and is *not* gitignored (the `#uv.lock` line stays commented), but it is currently untracked — stage it before relying on it for reproducibility. Use `uv sync --extra dev`.
- **`.vscode/settings.json` sets `python-envs.defaultEnvManager` to conda** — this contradicts the uv-based setup. Ignore it and use uv.
- **Hatchling** build backend; `[tool.hatch.build.targets.wheel] packages = ["src/llmsweep"]`.
- **Ruff**: line length 100, `src = ["src", "tests"]`, rule families `E F W I UP B C4 SIM RUF ASYNC PTH TID`, `B008` ignored, `E501` waived for `tests/*`. `llmsweep/lmstudio_agent_bench.py` is excluded — do not lint or fix it.
- **Deps**: `httpx`, `textual`, `rich`, `platformdirs`. Dev: `pytest`, `pytest-asyncio`, `ruff`, `mypy`, `textual-dev`, `respx`, `pytest-textual-snapshot`. Provider tests currently use `httpx.MockTransport` rather than `respx`; `respx` and `pytest-textual-snapshot` remain unused until the TUI milestone.
- **No `py.typed`** marker yet, despite the strict typing posture.

Config precedence (CLI > environment > project TOML > user TOML): `LLMSWEEP_HOST`, `LLMSWEEP_API_KEY`, `LLMSWEEP_TIMEOUT`, `LLMSWEEP_DATA_DIR`; project `llmsweep.toml` or `pyproject.toml`; user `~/.config/llmsweep/config.toml`. Credentials are scoped to the configured origin, redirects disabled, secrets redacted before logging, exporting, or persisting.

Exit codes: `0` success, `1` model error, `2` configuration/setup error, `3` regression-only failure, `4` non-retryable authentication failure. Selecting an unimplemented provider or requesting cost sorting exits 2. A scoring failure alone does NOT produce exit 1.

## Testing & QA

pytest, configured in `pyproject.toml`: `testpaths = ["tests"]`, `addopts = "-q --strict-markers"`, `asyncio_mode = "auto"`, `asyncio_default_fixture_loop_scope = "function"`, and **`filterwarnings = ["error"]`** — any warning fails the suite.

**Tests must run fully offline.** `tests/conftest.py` provides an autouse `no_network` fixture that monkeypatches `socket.socket.connect`, `socket.socket.connect_ex`, and `socket.getaddrinfo` to raise `AssertionError("tests must not open network connections")`. Provider tests inject `httpx.MockTransport` (`tests/fakes/lmstudio.py`) instead — never a real socket.

**Determinism through injection, not patching.** Clocks and sleeps are constructor arguments (`clock=`, `sleep=`), and `TurnRecorder.observe(event, now)` takes an explicit timestamp. Tests pass `sleep=no_wait` to skip backoff. Never `time.sleep`, never read the real clock in a test, never `monkeypatch` `time`/`random`/httpx.

Current suite: 13 collected tests across 4 files — 4 async test functions in `test_providers.py` (5 collected items via one `@pytest.mark.parametrize`), the rest sync. No skips.

- `tests/test_streams.py` — SSE fragmentation (one byte at a time), split UTF-8, mixed reasoning/text/tool deltas, usage-only endings, NDJSON yields nothing, malformed payload raises.
- `tests/test_selection.py` — index/range resolution, ambiguity, out-of-range, colon-containing IDs, metadata-not-name filtering, size sorting, scenario validation.
- `tests/test_metrics.py` — generation-window-only timing, reasoning-inclusive TTFT, token estimation, pooled throughput, percentile statistics, regression math.
- `tests/test_providers.py` — `test_provider_contract` parameterized over `["v1", "v0"]` drives the whole `Provider` protocol against `FakeLMStudio`; plus readiness polling vs. preexisting instances, retry-on-500 but never after a delta was emitted, and 401 never retrying.

Conventions: plain `def test_*() -> None` / `async def test_*() -> None` functions (no classes), one behavior per test with a descriptive name, `pytest.raises(SelectionError, match=r"...")` for error paths, `pytest.approx` for floats, `is None` for missing values. Tests import the package under its installed name (`from llmsweep.streams import ...`) and fakes via `from tests.fakes.lmstudio import ...` (both `tests/` and `tests/fakes/` have `__init__.py`). Module-level helpers are allowed for fixture construction (`packet(value)` in `test_streams.py`, `sse(value)` in `providers/lmstudio.py`).

Fake scripting: `fake.scripts` is a list of per-request responses — a `list[bytes]` SSE script or an `int` HTTP status. `completion("text")` builds a split content stream plus finish and usage frames; `calls(("get_weather", {...}))` builds fragmented tool-call frames. `fake.load_pending` simulates a load that is not immediately ready; `fake.disconnect` and `fake.gate` simulate mid-stream failure and stalled chunks.

**Testing gaps** — the spec mandates more than exists today: secret redaction assertions, scenario scoring (positive and negative cases, traversal, symlink escape, timeout), store schema migration, byte-identical persisted output from plain and TUI paths, and Textual pilot tests (widget state, worker isolation, cancellation cleanup, lossless resize, a synthetic 1000+ deltas/second stream). Every test must be deterministic and network-free.

When adding a test, prefer asserting observable behavior over implementation detail, and skip tests that merely assert field copies, defaults, or forwarding.
