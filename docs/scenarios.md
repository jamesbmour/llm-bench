---
title: "Benchmark Scenarios"
description: "Workload specifications for structured tool calling, agentic coding, and algorithmic code synthesis"
---

## Benchmark Scenarios

All scoring is deterministic. Each scenario repeat receives fresh conversation
history and a temporary workspace that is removed on completion, error, or cancellation.

| Scenario | Turns | Pass condition |
| --- | --- | --- |
| `weather` | 6 | All three tools called and final answer matches the Fahrenheit expression below. |
| `agent-code` | 10 | `write_file` called and the canonical tests pass on an independent rerun. |
| `codegen` | 1 | Extracted `fib(n)` passes every checker assertion within 15 seconds. |

---

### `weather` — Structured Tool Calling

The task requests Paris weather, conversion to Fahrenheit, and the current time in
`Europe/Paris`, followed by one closing sentence. The model must call all three tools
in a single turn (parallel tool calls are expected). Tools are defined with strict JSON
schemas:

| Tool | Parameters | Description |
| --- | --- | --- |
| `get_weather` | `{"city": string}` — required | Returns Paris weather: 18.0 °C, partly cloudy. Other cities return an error. |
| `convert_temperature` | `{"value": number, "from_unit": enum[c,f,k], "to_unit": enum[c,f,k]}` — all required | Converts between Celsius, Fahrenheit, and Kelvin; rounds to two decimals. Booleans are rejected as numbers. |
| `get_current_time` | `{"timezone": string}` — required | Returns ISO-8601 time for the given IANA timezone via `zoneinfo`. Invalid zones return a clean tool error. |

<Warning>
`convert_temperature` rejects boolean values for `value` (Python's `bool` is a subclass of `int`). Non-finite numbers and invalid unit strings raise errors that surface as `{"error": "..."}` in the conversation.
</Warning>

**Pass condition**: All three tool names must appear in `tools_called`, AND the final assistant answer must contain a case-insensitive match for:
```
64(\.4)?\s*(°|deg(rees)?)?\s*f
```
(i.e., "64.4 F", "64°F", "64 degrees fahrenheit", etc.). The time text is not independently scored — only the temperature conversion matters.

**Custom task mode**: `--task TEXT` replaces the default prompt with custom instructions and reports success as `n/a` (scoring disabled). When a custom task is used, scenario selection narrows to weather only; explicitly selecting other scenarios is rejected.

---

### `agent-code` — Autonomous Bug Fixing

The workspace contains three files: `README.md`, `buggy.py`, and `tests.py`. The defect
is in `total(items)` which uses `range(1, len(items))`, skipping the first element.

**Workspace contents:**

```python
# buggy.py (initial state — has a bug)
def total(items):
    value = 0
    for i in range(1, len(items)):
        value += items[i]
    return value
```

```python
# tests.py (canonical checker — recreated on each run_tests() call)
from buggy import total
assert total([1, 2, 3]) == 6
assert total([]) == 0
assert total([5]) == 5
assert total([-1, 1, 0]) == 0
assert total(range(100)) == sum(range(100))
print('all tests passed')
```

**Tools available:**

| Tool | Parameters | Description |
| --- | --- | --- |
| `list_files` | none | Lists workspace files. |
| `read_file` | `{"path": string}` — required | Reads up to 4,000 characters; reports truncation if exceeded. |
| `grep` | `{"pattern": string, "path"?: string}` — pattern required | Regex search across a file or all workspace files; caps at 50 matches and reports `"truncated"`. |
| `write_file` | `{"path": string, "content": string}` — both required | Writes only to `buggy.py`; any other path returns `{"error": "read-only path"}`. |
| `run_tests` | none | Runs the canonical test suite in an isolated subprocess. Returns `{"passed": bool, "output": str}`. |

**Path safety**: All tools reject absolute paths, `..` traversal segments, and symlinks that resolve outside the workspace root. The `path()` method resolves each path against the temporary workspace directory and verifies containment via `is_relative_to()`.

**Pass condition**: Both of these must be true:
1. `write_file` was called at least once (the model attempted a fix).
2. The independent test runner exits with code 0 AND its output ends with `"all tests passed"`.

The checker runs in a subprocess with `-I -B` flags, sanitized environment (`PATH` only), process-group isolation (`start_new_session=True`), and a 15-second timeout enforced via `asyncio.timeout`. Process groups are killed with `SIGKILL` to eliminate orphans. Early process exit (without reaching the completion marker) is not a pass.

---

### `codegen` — Algorithmic Code Synthesis

One completion is requested: a fenced Python block defining `fib(n)` that returns the nth
Fibonacci number as an `int`, with `fib(0)=0` and `fib(1)=1`.

**Code extraction**: The first fenced code block (``` ```python ... ```) is extracted from the model's answer. If no fence is present, the entire trimmed answer is used as-is. Unfenced valid Python may pass; prose-only or incomplete answers fail.

**Checker assertions:**
```python
assert fib(0) == 0
assert fib(1) == 1
assert [fib(i) for i in range(10)] == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
assert fib(20) == 6765
assert all(type(fib(i)) is int for i in range(10))
```

**Pass condition**: The checker exits with code 0 AND its output ends with `"all tests passed"`. A timeout (exceeding 15 seconds) reports `checker timed out` and fails. Negative inputs (`fib(-1)`) and large values (`fib(100)`) are not part of this benchmark — the checker does not test them.

---

### Scenario Skipping & Capability Detection

Models that explicitly advertise no tool support (via LM Studio's v1 capabilities) skip
the `weather` and `agent-code` scenarios, which report status `"skipped"` with output
`"model does not advertise tool capability"`. The `codegen` scenario is always attempted
regardless of advertised tool capability.

When a model's tool-use capability is unknown (`tool_use: null`), all three scenarios are
attempted and a warning is appended to the model result: `"tool capability unknown; attempting requested scenarios"`. Use `--require-tool-use` to narrow selection to models that explicitly advertise `tool_use: true`.

---

### Security & Isolation Guarantees

These subprocesses provide **workflow confinement**, not an OS-level security sandbox.
Generated Python executes with the current user's operating-system permissions inside a
temporary directory that is cleaned up on completion, error, or cancellation. The checker
subprocess uses:

- **`-I` flag**: Isolated mode — ignores `PYTHONSTARTUP`, user site-packages, and `.pth` files.
- **`-B` flag**: No bytecode (`.pyc`) generation.
- **Sanitized environment**: Only `PATH` (set to `os.defpath`) and `LANG=C.UTF-8`.
- **`start_new_session=True`**: New process group for clean `SIGKILL` termination via `killpg`.
- **15-second timeout**: Enforced by `asyncio.timeout`; on expiry, the entire process group is killed.

- For hostile-code protection, run `llmsweep` inside a containerized or virtualized host environment.


For the complete schema reference of how scenario results are persisted in transcripts and run documents, see [Data Formats & Exports](data_formats.md).