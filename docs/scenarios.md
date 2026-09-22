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

### `weather`

The task requests Paris weather, conversion to Fahrenheit, and the current time
in `Europe/Paris`, followed by one closing sentence. Tools are:

- `get_weather(city)`: Paris returns 18.0 °C, partly cloudy.
- `convert_temperature(value, from_unit, to_unit)`: supports `c`, `f`, and `k`; rounds to two decimals.
- `get_current_time(timezone)`: uses `zoneinfo`; invalid zones return a clean tool error.

Pass requires all three tool names and a case-insensitive match of
`64(\.4)?\s*(°|deg(rees)?)?\s*f` in the final answer. The time text is not independently
scored. `--task` selects weather with custom instructions and reports success as `n/a`.

### `agent-code`

The workspace contains `README.md`, `buggy.py`, and `tests.py`. The defect is
`range(1, len(items))` in `total(items)`, which skips the first value.

- `list_files()` lists workspace files.
- `read_file(path)` truncates at 4,000 characters.
- `grep(pattern, path?)` caps matches at 50 and reports `truncated`.
- `write_file(path, content)` accepts only `buggy.py`; other files return a read-only error.
- `run_tests()` returns `passed` and the last 500 output characters.

Tools reject absolute paths, traversal, and paths resolving outside the workspace.
The independent canonical tests assert totals for `[1,2,3]`, `[]`, `[5]`, `[-1,1,0]`,
and `range(100)`. A write call and a successful checker exit are both required.
The checker must reach its completion marker; early process exit is not a pass.

### `codegen`

One completion is asked for a fenced Python block defining `fib(n)`. The first
fenced block is extracted; without a fence, the whole trimmed answer is used.
Unfenced valid Python may pass; prose and `return n` fail. The checker asserts
`fib(0)==0`, `fib(1)==1`, the first ten Fibonacci numbers, `fib(20)==6765`, and exact
`int` return types for the first ten values. Timeout reports `checker timed out`.
Negative inputs and `fib(100)` are not part of this benchmark.

Models explicitly advertising no tool support skip the two tool scenarios (`n/a`),
with no expected tools added. Unknown capability is attempted unless
`--require-tool-use` was requested.

These subprocesses provide workflow confinement, not an OS security sandbox.
Generated Python still executes with the current user's operating-system permissions.

---
