# Benchmark Scenarios

`llmsweep` tests models against three distinct workloads that probe tool-calling fidelity, multi-step problem solving, and raw code generation.

---

## 1. Scenario Summary

| Scenario | Paradigm | Default Turns | Tool Calling | Primary Verification |
| :--- | :--- | :---: | :---: | :--- |
| **`weather`** | Multi-Turn Tool Use | 6 | `get_weather` | Tool schema compliance + regex temperature verification. |
| **`agent-code`** | Autonomous Agentic Coding | 10 | Filesystem & Search Tools | Guarded file modification + independent test suite rerun. |
| **`codegen`** | Algorithmic Code Synthesis | 1 | None | Subprocess execution with strict test cases and 15s timeout. |

---

## 2. `weather` — Structured Tool Calling

The `weather` scenario assesses a model's ability to interpret system instructions, formulate syntactically valid JSON tool calls matching OpenAI function schemas, parse tool responses, and synthesize a coherent natural-language reply.

### Tool Definition
The model is supplied with a mock weather service tool:

```json
{
  "name": "get_weather",
  "description": "Get the current weather and temperature for a given city.",
  "parameters": {
    "type": "object",
    "properties": {
      "location": {
        "type": "string",
        "description": "The city and state/country, e.g. 'San Francisco, CA' or 'Tokyo'."
      },
      "unit": {
        "type": "string",
        "enum": ["celsius", "fahrenheit"],
        "description": "Temperature scale to return."
      }
    },
    "required": ["location"]
  }
}
```

### Execution Flow
1. **User Query**: The model is asked to compare the weather or temperature between target locations (e.g., *"What is the weather in Tokyo and Paris right now?"*).
2. **Tool Invocation**: The model must issue one or more valid `get_weather` calls with expected location parameters.
3. **Simulated Return**: The runner executes the mock tool and returns realistic JSON data back to the model as a `tool` role message.
4. **Final Synthesis**: The model summarizes the results in a conversational response.

### Scoring & Verification
- **Tool Use Check**: Validates that `get_weather` was invoked with valid arguments and expected location targets.
- **Regex Temperature Validation**: Analyzes the final assistant response with regular expressions to ensure the temperatures returned by the tool are accurately reported without hallucinations.
- **Custom Task Flag (`--task`)**: Supplying `--task "Custom prompt..."` allows users to test arbitrary single-tool interactions. When `--task` is supplied, automatic scoring is disabled and cannot be combined with other scenarios.

---

## 3. `agent-code` — Agentic Coding & Bug Fixing

The `agent-code` scenario models an interactive developer agent working in an unfamiliar repository. The model must navigate a workspace, locate an error, modify code, and verify its changes.

### Workspace Setup
A clean temporary workspace containing a small Python project with a deliberate software defect and a failing unit test suite is initialized for each scenario run.

### Provided Agent Tools
The model is equipped with four workflow tools:

1. **`list_files(directory: str = ".")`**:
   - Lists files and subdirectories relative to the workspace root.
2. **`read_file(path: str)`**:
   - Reads the textual contents of a specified file. Enforces a maximum byte and line count limit to prevent context-window flooding.
3. **`grep_files(query: str, path: str = ".")`**:
   - Searches for regular expressions across repository files with bounded match output.
4. **`write_file(path: str, content: str)`**:
   - Overwrites or creates files within the workspace.

### Security Guards & Confinement
- **Path Traversal Protection**: Any path containing `..`, absolute paths outside the workspace, or symlink traversal attempts are blocked immediately.
- **Read-Only Enforcements**: Modifying test fixtures, configuration files, or files outside the target code area raises an actionable tool error.
- **Mandatory Mutation**: The model must invoke `write_file` at least once to demonstrate an intentional bug fix.

### Verification Criteria
Once the model indicates completion (or exhausts its turn budget):
1. The modified workspace is inspected to verify that `write_file` was called and target files were updated.
2. **Independent Test Rerun**: The runner executes the workspace's unit test suite in an isolated subprocess with a sanitized environment.
3. If all tests pass with zero exit codes, the scenario is scored as a **Pass**. If tests fail, time out, or the model produces no edits, it is scored as a **Fail**.

---

## 4. `codegen` — Algorithmic Code Synthesis

The `codegen` scenario evaluates raw instruction following, algorithm design, and syntax precision without access to external tools.

### Task Specification
The model receives a precise prompt instructing it to implement an optimal Fibonacci function (`fib(n: int) -> int`):
- Must return $F(n)$ correctly for $n \ge 0$.
- Must handle base cases ($F(0) = 0$, $F(1) = 1$).
- Must handle edge cases (e.g. negative inputs raise `ValueError`).
- Must operate efficiently up to large numbers ($n = 100$) within compute and memory limits.

### Code Extraction Engine
The runner parses the model's single-turn completion:
- **Fenced Blocks**: Prefers standard markdown code blocks (````python ... ```` or ````py ... ````).
- **Unfenced Fallback**: If no code fence is present, the parser checks whether the entire output constitutes valid, compilable Python source code. Unfenced prose or conversational preamble causes immediate scoring failure.

### Subprocess Verification Sandbox
The extracted code is saved to a clean temporary file and imported by an automated test harness executed in a subprocess:
- **Sanitized Environment**: Clears `PYTHONPATH` and isolates execution.
- **Strict Timeout**: Execution is capped at 15.0 seconds. Infinite recursion or non-terminating loops are terminated via process-group signals (`SIGKILL`).
- **Correctness Battery**: Evaluates base values, intermediate values, and large numbers against mathematical constants.
- **Scoring**: Full pass requires 100% test case satisfaction and successful zero-code subprocess exit.

---

## 5. Scenario State & Isolation Guarantees

Every scenario repeat is strictly isolated:
- **Fresh Working Directory**: Temporary directories are uniquely generated for each repeat and destroyed upon completion.
- **Subprocess Isolation**: Generated code never executes within the `llmsweep` process space.
- **Context Clearing**: Conversation message history is discarded between repeats and scenario transitions; models are never evaluated using contaminated context from previous tests.
