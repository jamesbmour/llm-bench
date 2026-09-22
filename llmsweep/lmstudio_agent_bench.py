#!/usr/bin/env python3
"""Benchmark agentic (tool-calling) and coding performance of LM Studio models.

Lists chat models from the LM Studio local server (embeddings/reranker models
are always excluded), lets you pick which to test — by number, by name, or by
ranges like 1-3,5,7-10 — then for each model: load -> warm up -> run each
selected scenario (repeated with --repeat) -> unload. Scenarios: weather
(multi-step tool-calling), agent-code (inspect a workspace, fix a bug, verify
with tests), codegen (write a correct fib() from spec, no tools). Reports load
time, time-to-first-token, tokens/s, tool-call correctness, tool errors, and
total wall time per model, plus a comparison table.

Uses rich for tables, rules, and status spinners when installed
(pip install rich); falls back to plain text otherwise. Requires a running
LM Studio server (default http://localhost:1234).

Examples:
  python3 scripts/lmstudio_agent_bench.py                  # interactive selection
  python3 scripts/lmstudio_agent_bench.py --all            # test every chat model
  python3 scripts/lmstudio_agent_bench.py --models qwen3.8-27b,qwen3-coder
  python3 scripts/lmstudio_agent_bench.py --models 1-3,5,7-10   # by list index
  python3 scripts/lmstudio_agent_bench.py --all --scenarios weather,agent-code,codegen
  python3 scripts/lmstudio_agent_bench.py --models qwen3-coder --scenarios agent-code,codegen --repeat 2
  python3 scripts/lmstudio_agent_bench.py --all --json results.json
  python3 scripts/lmstudio_agent_bench.py --all --repeat 3 --sort-by tok_s
  python3 scripts/lmstudio_agent_bench.py --all --csv r.csv --markdown r.md
  python3 scripts/lmstudio_agent_bench.py --all --baseline previous.json
  python3 scripts/lmstudio_agent_bench.py --list --require-tool-use
  python3 scripts/lmstudio_agent_bench.py --show results.json
  python3 scripts/lmstudio_agent_bench.py --show results-dir/ --sort-by tok_s
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from rich.console import Console
    from rich.prompt import Prompt
    from rich.table import Table
    from rich.text import Text

    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

CONSOLE = Console() if HAVE_RICH else None


def _say(text: str, style: str | None = None) -> None:
    if CONSOLE is not None:
        CONSOLE.print(text, style=style, markup=False)
    else:
        print(text)


def _rule(title: str, style: str = "bold blue") -> None:
    if CONSOLE is not None:
        CONSOLE.rule(title, style=style)
    else:
        print(f"\n{title}")


def _status(message: str):
    if CONSOLE is not None:
        return CONSOLE.status(message)
    return contextlib.nullcontext()


def _model_table(numbered: bool = False) -> Table:
    table = Table(show_header=True, header_style="bold cyan", border_style="dim")
    if numbered:
        table.add_column("#", justify="right", style="dim")
    table.add_column("Model", overflow="fold")
    table.add_column("Quant", style="dim")
    table.add_column("Size", justify="right", style="dim")
    table.add_column("State")
    table.add_column("Capabilities")
    return table


def _add_model_row(table: Table, number: int | None, meta: dict) -> None:
    caps = ",".join(meta.get("capabilities") or []) or "-"
    state = meta.get("state", "?")
    cells: list = [str(number)] if number is not None else []
    cells.append(Text(meta["id"]))
    cells.append(Text(meta.get("quantization") or "?", style="dim"))
    cells.append(Text(_size_str(meta["id"]), style="dim"))
    cells.append(Text(state, style="green" if state == "loaded" else "dim"))
    cells.append(
        Text(
            caps,
            style="green" if "tool_use" in (meta.get("capabilities") or []) else "dim",
        )
    )
    table.add_row(*cells)

SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([bBmM])\b")


def _model_size_b(model_id: str) -> float | None:
    sizes = []
    for num, unit in SIZE_RE.findall(model_id):
        try:
            val = float(num)
        except ValueError:
            continue
        sizes.append(val if unit in "bB" else val / 1000.0)
    return max(sizes) if sizes else None


def _size_str(model_id: str) -> str:
    size = _model_size_b(model_id)
    if size is None:
        return "-"
    if size >= 1:
        return f"{size:.1f}".rstrip("0").rstrip(".") + "B"
    return f"{size * 1000:.0f}M"


def sort_models_by_size(models: list[dict]) -> list[dict]:
    def key(m: dict):
        size = _model_size_b(m.get("id", ""))
        return (size is None, -(size or 0.0), m.get("id", "").lower())
    return sorted(models, key=key)

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 1234

SYSTEM_PROMPT = (
    "You are an efficient assistant with access to tools. "
    "Use the provided tools to gather facts, then answer concisely. "
    "Call a tool only when you need information you do not already have."
)

DEFAULT_TASK = (
    "Use the tools to answer this: What is the current weather in Paris? "
    "Convert that temperature to Fahrenheit. Then look up the current time "
    "in Europe/Paris. Finish with a single sentence that includes the "
    "temperature in both units and the current time."
)

EXPECTED_TOOLS = ("get_weather", "convert_temperature", "get_current_time")
# 18 C (the canned weather value) -> 64.4 F
CONVERTED_RE = re.compile(r"64(?:\.4)?\s*(?:\u00b0|deg(?:rees)?)?\s*f", re.I)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "City name"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_temperature",
            "description": "Convert a temperature between Celsius (c), Fahrenheit (f), and Kelvin (k).",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "number"},
                    "from_unit": {"type": "string", "enum": ["c", "f", "k"]},
                    "to_unit": {"type": "string", "enum": ["c", "f", "k"]},
                },
                "required": ["value", "from_unit", "to_unit"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current date and time in an IANA timezone (e.g. Europe/Paris).",
            "parameters": {
                "type": "object",
                "properties": {"timezone": {"type": "string"}},
                "required": ["timezone"],
            },
        },
    },
]


def execute_tool(name: str, args: dict) -> dict:
    if name == "get_weather":
        city = str(args.get("city", "Paris"))
        return {"city": city, "temperature_c": 18.0, "condition": "partly cloudy"}
    if name == "convert_temperature":
        try:
            value = float(args.get("value"))
            from_unit = str(args.get("from_unit", "c")).lower()
            to_unit = str(args.get("to_unit", "f")).lower()
        except (TypeError, ValueError):
            return {"error": "invalid arguments: need numeric value and units c/f/k"}
        if from_unit == "c":
            celsius = value
        elif from_unit == "f":
            celsius = (value - 32) * 5 / 9
        elif from_unit == "k":
            celsius = value - 273.15
        else:
            return {"error": f"unknown from_unit {from_unit!r}"}
        if to_unit == "c":
            out = celsius
        elif to_unit == "f":
            out = celsius * 9 / 5 + 32
        elif to_unit == "k":
            out = celsius + 273.15
        else:
            return {"error": f"unknown to_unit {to_unit!r}"}
        return {"value": round(out, 2), "unit": to_unit}
    if name == "get_current_time":
        tz = str(args.get("timezone", "UTC"))
        try:
            now = datetime.now(ZoneInfo(tz))
        except Exception:
            return {"error": f"unknown timezone {tz!r}"}
        return {"timezone": tz, "time": now.strftime("%Y-%m-%d %H:%M:%S %Z")}
    return {"error": f"unknown tool {name!r}"}

CODE_FIXTURE = {
    "README.md": (
        "# Buggy total\n\n"
        "`total(items)` in `buggy.py` should return the sum of all items. "
        "It currently skips the first element. Fix it, then verify with "
        "the `run_tests` tool.\n"
    ),
    "buggy.py": (
        "def total(items):\n"
        "    s = 0\n"
        "    for i in range(1, len(items)):\n"
        "        s += items[i]\n"
        "    return s\n"
    ),
    "tests.py": (
        "import os\n"
        "import sys\n"
        "sys.path.insert(0, os.path.dirname(__file__))\n"
        "from buggy import total\n"
        "assert total([1, 2, 3]) == 6\n"
        "assert total([]) == 0\n"
        "assert total([5]) == 5\n"
        "assert total([-1, 1, 0]) == 0\n"
        "assert total(range(100)) == sum(range(100))\n"
        'print("all tests passed")\n'
    ),
}

CODE_WRITABLE = {"buggy.py"}

CODEGEN_CHECKER = (
    "import os\n"
    "import sys\n"
    "sys.path.insert(0, os.path.dirname(__file__))\n"
    "from solution import fib\n"
    "assert fib(0) == 0\n"
    "assert fib(1) == 1\n"
    "assert [fib(i) for i in range(10)] == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]\n"
    "assert fib(20) == 6765\n"
    "assert all(type(fib(i)) is int for i in range(10))\n"
    'print("all tests passed")\n'
)

CODE_BLOCK_RE = re.compile(r"```(?:python\s+)?(.*?)```", re.S)

AGENT_CODE_SYSTEM = (
    "You are an efficient coding agent with access to file and test tools. "
    "Inspect the workspace, make the minimal fix, verify with the test tool, "
    "then summarize briefly. Call a tool only when you need information you "
    "do not already have."
)

AGENT_CODE_TASK_BODY = (
    "1. Inspect the workspace (list_files, read_file, grep).\n"
    "2. `buggy.py` has a function that returns the wrong result; "
    "`README.md` describes the expected behavior.\n"
    "3. Fix it with write_file (only `buggy.py` is writable).\n"
    "4. Run run_tests to verify, then finish with one sentence "
    "describing the fix."
)

CODEGEN_SYSTEM = (
    "You are a precise Python programmer. Follow the specification exactly "
    "and reply with only the requested code block."
)

CODEGEN_TASK = (
    "Write a Python function `fib(n)` that returns the nth Fibonacci number "
    "with fib(0) == 0 and fib(1) == 1. Reply with exactly one ```python "
    "fenced code block containing only the function definition "
    "(no explanation, no extra text)."
)


def _safe_path(root: str, path: str) -> str | None:
    full = os.path.normpath(os.path.join(root, path or ""))
    if full != root and not full.startswith(root + os.sep):
        return None
    return full


def _run_checker(exe: str, script: str, cwd: str, timeout: int) -> tuple[bool, str]:
    try:
        cp = subprocess.run(
            [exe, script], cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return False, "checker timed out"
    except Exception as e:
        return False, f"checker failed: {e}"[:200]
    out = (cp.stdout + cp.stderr)[-500:]
    return cp.returncode == 0, out.strip() or "(no output)"


WORKSPACE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the task workspace.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a workspace file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search workspace files for a regex pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "optional file to search"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Overwrite a writable workspace file (only buggy.py).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run the workspace test suite.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def execute_workspace(name: str, args: dict, ctx: dict) -> dict:
    root = ctx["root"]
    if name == "list_files":
        return {"files": sorted(CODE_FIXTURE)}
    if name == "read_file":
        full = _safe_path(root, str(args.get("path", "")))
        if full is None or not os.path.isfile(full):
            return {"error": f"no such file {args.get('path')!r}"}
        try:
            with open(full) as f:
                return {"path": args.get("path"), "content": f.read()[:4000]}
        except OSError as e:
            return {"error": str(e)[:200]}
    if name == "grep":
        try:
            rx = re.compile(str(args.get("pattern", "")))
        except re.error as e:
            return {"error": f"bad pattern: {e}"}
        only = str(args.get("path") or "")
        rels = [only] if only else sorted(CODE_FIXTURE)
        hits = []
        for rel in rels:
            full = _safe_path(root, rel)
            if full is None or not os.path.isfile(full):
                return {"error": f"no such file {rel!r}"}
            try:
                with open(full) as f:
                    text = f.read()
            except OSError as e:
                return {"error": str(e)[:200]}
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{i}:{line.strip()[:160]}")
                    if len(hits) >= 50:
                        return {"matches": hits, "truncated": True}
        return {"matches": hits}
    if name == "write_file":
        rel = str(args.get("path", ""))
        if rel not in CODE_WRITABLE:
            return {
                "error": f"{rel!r} is read-only (writable: {sorted(CODE_WRITABLE)})"
            }
        full = _safe_path(root, rel)
        if full is None:
            return {"error": f"invalid path {rel!r}"}
        try:
            with open(full, "w") as f:
                f.write(str(args.get("content", "")))
        except OSError as e:
            return {"error": str(e)[:200]}
        return {"path": rel, "bytes": len(str(args.get("content", "")))}
    if name == "run_tests":
        ok, out = _run_checker(
            sys.executable, os.path.join(root, "tests.py"), root, 30
        )
        return {"passed": ok, "output": out}
    return {"error": f"unknown tool {name!r}"}


def setup_workspace() -> dict:
    root = tempfile.mkdtemp(prefix="agent-bench-")
    for rel, content in CODE_FIXTURE.items():
        with open(os.path.join(root, rel), "w") as f:
            f.write(content)
    return {"root": root}


def teardown_workspace(ctx: dict) -> None:
    shutil.rmtree(ctx["root"], ignore_errors=True)


def _agent_code_task(ctx: dict) -> str:
    return f"Fix the bug in this workspace: {ctx['root']}\n\n" + AGENT_CODE_TASK_BODY


def score_workspace(out: dict, ctx: dict, task: str) -> bool:
    if "write_file" not in out.get("tools_called", []):
        return False
    ok, _ = _run_checker(
        sys.executable, os.path.join(ctx["root"], "tests.py"), ctx["root"], 30
    )
    return ok


def extract_code(answer: str | None) -> str | None:
    if not answer:
        return None
    m = CODE_BLOCK_RE.search(answer)
    if m:
        return m.group(1).strip()
    return answer.strip() or None


def score_codegen(out: dict, ctx: dict, task: str) -> bool:
    code = extract_code(out.get("final_answer"))
    if not code:
        return False
    root = tempfile.mkdtemp(prefix="codegen-bench-")
    try:
        with open(os.path.join(root, "solution.py"), "w") as f:
            f.write(code)
        with open(os.path.join(root, "check.py"), "w") as f:
            f.write(CODEGEN_CHECKER)
        ok, _ = _run_checker(
            sys.executable, os.path.join(root, "check.py"), root, 15
        )
        return ok
    finally:
        shutil.rmtree(root, ignore_errors=True)


@dataclass
class Scenario:
    name: str
    description: str
    expected_tools: tuple = ()
    tools_enabled: bool = True
    system_prompt: str = SYSTEM_PROMPT
    max_turns: int | None = None
    setup: Callable = lambda: {}
    teardown: Callable = lambda ctx: None
    task: Callable = lambda ctx: DEFAULT_TASK
    tools: Callable = lambda ctx: TOOLS
    execute: Callable = lambda n, a, c: execute_tool(n, a)
    score: Callable = lambda out, ctx, task: None


SCENARIOS = {
    "weather": Scenario(
        name="weather",
        description="multi-step tool calling: weather lookup, unit conversion, time lookup",
        expected_tools=EXPECTED_TOOLS,
        score=lambda out, ctx, task: (
            None
            if task != DEFAULT_TASK
            else evaluate(out.get("tools_called", []), out.get("final_answer"), True)
        ),
    ),
    "agent-code": Scenario(
        name="agent-code",
        description="agentic coding: inspect a workspace, fix a buggy function, verify with tests",
        expected_tools=("read_file", "write_file", "run_tests"),
        system_prompt=AGENT_CODE_SYSTEM,
        max_turns=10,
        setup=setup_workspace,
        teardown=teardown_workspace,
        task=_agent_code_task,
        tools=lambda ctx: WORKSPACE_TOOLS,
        execute=execute_workspace,
        score=score_workspace,
    ),
    "codegen": Scenario(
        name="codegen",
        description="coding: write a correct fib() from spec, no tools",
        tools_enabled=False,
        system_prompt=CODEGEN_SYSTEM,
        task=lambda ctx: CODEGEN_TASK,
        tools=lambda ctx: None,
        score=score_codegen,
    ),
}


def resolve_scenarios(names: str) -> list[Scenario]:
    picked: list[Scenario] = []
    for part in names.split(","):
        part = part.strip()
        if not part:
            continue
        sc = SCENARIOS.get(part)
        if sc is None:
            die(
                f"unknown scenario {part!r} (choose from: {', '.join(SCENARIOS)})"
            )
        if sc not in picked:
            picked.append(sc)
    if not picked:
        die("no scenarios selected")
    return picked



EMBEDDING_TYPES = {"embedding", "embeddings", "reranker"}


def is_chat_model(meta: dict) -> bool:
    return (meta.get("type") or "llm").lower() not in EMBEDDING_TYPES


class LMStudioError(RuntimeError):
    pass


class LMStudio:
    def __init__(
        self, host: str, port: int, api_key: str | None = None, timeout: int = 300
    ):
        self.base = f"http://{host}:{port}"
        self.api_key = api_key
        self.timeout = timeout
        self.api: str | None = None

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = self.base + path
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url, data=data, headers=self._headers(), method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            hint = (
                " (pass --api-key if the server requires a token)"
                if e.code in (401, 403)
                else ""
            )
            raise LMStudioError(f"HTTP {e.code} {method} {path}: {detail}{hint}") from None
        except urllib.error.URLError as e:
            raise LMStudioError(f"cannot reach {url}: {e.reason}") from None
        return json.loads(body) if body.strip() else {}

    @staticmethod
    def _normalize_v1(m: dict) -> dict:
        caps = m.get("capabilities") or {}
        cap_list = []
        if caps.get("vision"):
            cap_list.append("vision")
        if caps.get("trained_for_tool_use"):
            cap_list.append("tool_use")
        loaded = m.get("loaded_instances") or []
        quant = m.get("quantization")
        return {
            "id": m.get("key") or m.get("display_name"),
            "object": "model",
            "type": (m.get("type") or "llm").lower(),
            "publisher": m.get("publisher"),
            "arch": m.get("architecture"),
            "quantization": quant.get("name") if isinstance(quant, dict) else quant,
            "state": "loaded" if loaded else "not-loaded",
            "max_context_length": m.get("max_context_length"),
            "capabilities": cap_list,
            "loaded_instances": loaded,
            "selected_variant": m.get("selected_variant"),
        }

    def list_models(self) -> list[dict]:
        if self.api != "v0":
            try:
                models = self._request("GET", "/api/v1/models").get("models", [])
                self.api = "v1"
                return [self._normalize_v1(m) for m in models]
            except LMStudioError:
                if self.api == "v1":
                    raise
        data = self._request("GET", "/api/v0/models").get("data", [])
        self.api = "v0"
        return data

    def model_state(self, model_id: str) -> str | None:
        for m in self.list_models():
            if m.get("id") == model_id:
                return m.get("state")
        return None

    def load(self, model_id: str) -> dict:
        if self.api == "v0":
            raise LMStudioError(
                "model load/unload needs the v1 REST API (LM Studio 0.4+); "
                "relying on JIT loading"
            )
        return self._request("POST", "/api/v1/models/load", {"model": model_id})

    def unload(self, model_id: str, instance_id: str | None = None) -> None:
        if self.api == "v0":
            return
        self._request(
            "POST", "/api/v1/models/unload", {"instance_id": instance_id or model_id}
        )

    def wait_loaded(self, model_id: str, timeout: int = 600) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.model_state(model_id) == "loaded":
                return
            time.sleep(1.0)
        raise LMStudioError(f"model {model_id!r} not loaded after {timeout}s")

    def chat(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 1024,
        stream: bool = False,
    ):
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = tools
        if not stream:
            return self._request("POST", "/v1/chat/completions", payload)
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers=self._headers(),
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise LMStudioError(f"HTTP {e.code} chat: {detail}") from None
        except urllib.error.URLError as e:
            raise LMStudioError(f"cannot reach {self.base}: {e.reason}") from None
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue


@dataclass
class ModelResult:
    model: str
    quantization: str = ""
    tool_use: bool = False
    repeats: int = 1
    load_s: float | None = None
    warmup_s: float | None = None
    ttft_ms: float | None = None
    ttft_ms_min: float | None = None
    ttft_ms_max: float | None = None
    tok_s: float | None = None
    tok_s_min: float | None = None
    tok_s_max: float | None = None
    total_tokens: int = 0
    turns: int = 0
    tools_called: list[str] = field(default_factory=list)
    expected_tools: int = 0
    success: bool | None = None
    success_rate: float | None = None
    tool_errors: int = 0
    malformed_args: int = 0
    unknown_tools: int = 0
    final_answer: str | None = None
    runs: list[dict] = field(default_factory=list)
    scenario_runs: list[dict] = field(default_factory=list)
    total_s: float = 0.0
    error: str | None = None


def run_benchmark(
    lm: LMStudio,
    model: str,
    task: str,
    max_turns: int,
    max_tokens: int,
    system: str = SYSTEM_PROMPT,
    tools: list[dict] | None = None,
    execute=execute_tool,
    verbose: bool = False,
) -> dict:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    known = {t["function"]["name"] for t in (tools or [])}
    ttft_ms: float | None = None
    total_tokens = 0
    total_gen_s = 0.0
    tools_called: list[str] = []
    final_answer: str | None = None
    turns = 0
    malformed_args = 0
    unknown_tools = 0
    turn_s: list[float] = []

    for _ in range(max_turns):
        t0 = time.perf_counter()
        first_tok: float | None = None
        content_parts: list[str] = []
        tcs: dict[int, dict] = {}
        usage_tokens: int | None = None
        est_tokens = 0

        for chunk in lm.chat(
            model, messages, tools=tools, max_tokens=max_tokens, stream=True
        ):
            if chunk.get("usage"):
                usage_tokens = chunk["usage"].get("completion_tokens")
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            got = False
            if delta.get("content"):
                content_parts.append(delta["content"])
                got = True
            if delta.get("reasoning") or delta.get("reasoning_content"):
                got = True
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tcs.setdefault(idx, {"id": None, "name": None, "args": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
                got = True
            if got:
                est_tokens += 1
                if first_tok is None:
                    first_tok = time.perf_counter()

        gen_s = time.perf_counter() - t0
        total_gen_s += gen_s
        turn_s.append(gen_s)
        total_tokens += usage_tokens or est_tokens
        if ttft_ms is None and first_tok is not None:
            ttft_ms = (first_tok - t0) * 1000.0
        turns += 1

        if tcs:
            assistant_tool_calls = []
            for idx in sorted(tcs):
                slot = tcs[idx]
                name = slot["name"] or "unknown"
                raw_args = slot["args"] or "{}"
                tools_called.append(name)
                assistant_tool_calls.append(
                    {
                        "id": slot["id"] or f"call_{turns}_{idx}",
                        "type": "function",
                        "function": {"name": name, "arguments": raw_args},
                    }
                )
            messages.append(
                {
                    "role": "assistant",
                    "content": "".join(content_parts) or None,
                    "tool_calls": assistant_tool_calls,
                }
            )
            for tc in assistant_tool_calls:
                name = tc["function"]["name"]
                if name not in known:
                    unknown_tools += 1
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}
                    malformed_args += 1
                if verbose:
                    _say(f"    -> {name} {tc['function']['arguments'][:200]}", "cyan")
                result = execute(name, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result),
                    }
                )
                if verbose:
                    _say(f"    <- {json.dumps(result)[:200]}", "dim")
            _say(
                f"  turn {turns}: {len(assistant_tool_calls)} tool call(s) "
                f"[{', '.join(tc['function']['name'] for tc in assistant_tool_calls)}] "
                f"in {gen_s:.1f}s",
                "cyan",
            )
            continue

        final_answer = "".join(content_parts).strip()
        _say(f"  turn {turns}: final answer in {gen_s:.1f}s", "green")
        break

    tok_s = total_tokens / total_gen_s if total_gen_s > 0 else None
    return {
        "ttft_ms": ttft_ms,
        "tok_s": tok_s,
        "total_tokens": total_tokens,
        "turns": turns,
        "tools_called": tools_called,
        "final_answer": final_answer,
        "malformed_args": malformed_args,
        "unknown_tools": unknown_tools,
        "turn_s": turn_s,
        "transcript": messages,
    }


def evaluate(
    tools_called: list[str], final_answer: str | None, scored: bool
) -> bool | None:
    if not scored:
        return None
    called = set(tools_called)
    all_called = all(t in called for t in EXPECTED_TOOLS)
    answer_ok = bool(final_answer) and bool(CONVERTED_RE.search(final_answer))
    return all_called and answer_ok


def save_transcript(
    directory: str, model: str, repeat: int, transcript: list[dict], scenario: str | None = None
) -> None:
    os.makedirs(directory, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", model)
    name = f"{safe}_{scenario}_r{repeat}" if scenario else f"{safe}_r{repeat}"
    path = os.path.join(directory, f"{name}.json")
    with open(path, "w") as f:
        json.dump({"model": model, "repeat": repeat, "messages": transcript}, f, indent=2)
    _say(f"  transcript: {path}", "dim")


def bench_one(
    lm: LMStudio,
    meta: dict,
    args: argparse.Namespace,
    index: int,
    total: int,
    scenarios: list[Scenario],
) -> ModelResult:
    mid = meta["id"]
    quant = meta.get("quantization") or "?"
    has_tools = "tool_use" in (meta.get("capabilities") or [])
    _rule(f"[{index}/{total}] {mid}  ({quant}, tool_use={'yes' if has_tools else 'no'})")
    multi = len(scenarios) > 1
    if multi:
        _say(f"  scenarios: {', '.join(sc.name for sc in scenarios)}", "dim")
    res = ModelResult(model=mid, quantization=quant, tool_use=has_tools)
    res.repeats = args.repeat
    t_start = time.perf_counter()
    we_loaded = False
    instance_id: str | None = None
    try:
        if lm.model_state(mid) != "loaded":
            t0 = time.perf_counter()
            with _status(f"Loading {mid}"):
                info = lm.load(mid)
                instance_id = info.get("instance_id")
                lm.wait_loaded(mid, timeout=args.load_timeout)
            res.load_s = (
                info.get("load_time_seconds") or time.perf_counter() - t0
            )
            we_loaded = True
            _say(f"  loaded in {res.load_s:.1f}s", "green")
        else:
            _say("  already loaded", "dim")

        if not args.no_warmup:
            t0 = time.perf_counter()
            with _status("Warming up"):
                lm.chat(
                    mid,
                    [{"role": "user", "content": "Reply with exactly: ok"}],
                    max_tokens=8,
                )
            res.warmup_s = time.perf_counter() - t0
            _say(f"  warmup: {res.warmup_s:.1f}s", "dim")

        outs: list[dict] = []
        groups: list = []
        for sc in scenarios:
            offered = has_tools and sc.tools_enabled
            sc_outs: list[dict] = []
            sc_success: list = []
            for rep in range(1, args.repeat + 1):
                if multi and args.repeat > 1:
                    _say(f"  [{sc.name}] repeat {rep}/{args.repeat}", "bold")
                elif multi:
                    _say(f"  [{sc.name}]", "bold")
                elif args.repeat > 1:
                    _say(f"  repeat {rep}/{args.repeat}", "bold")
                ctx = sc.setup()
                try:
                    task_text = args.task if sc.name == "weather" else sc.task(ctx)
                    out = run_benchmark(
                        lm,
                        mid,
                        task_text,
                        sc.max_turns or args.max_turns,
                        args.max_tokens,
                        system=sc.system_prompt,
                        tools=sc.tools(ctx) if offered else None,
                        execute=lambda n, a, _s=sc, _c=ctx: _s.execute(n, a, _c),
                        verbose=args.verbose,
                    )
                    success = sc.score(out, ctx, task_text)
                finally:
                    sc.teardown(ctx)
                sc_outs.append(out)
                outs.append(out)
                sc_success.append(success)
                if args.transcript_dir:
                    save_transcript(
                        args.transcript_dir,
                        mid,
                        rep,
                        out["transcript"],
                        sc.name if multi else None,
                    )
                if out["final_answer"]:
                    shown = (
                        out["final_answer"] if args.verbose else out["final_answer"][:160]
                    )
                    _say(f"  {'[' + sc.name + '] ' if multi else ''}answer: {shown}")
            groups.append((sc, sc_outs, sc_success))

        tok_vals = [o["tok_s"] for o in outs if o["tok_s"] is not None]
        ttft_vals = [o["ttft_ms"] for o in outs if o["ttft_ms"] is not None]
        res.tok_s = sum(tok_vals) / len(tok_vals) if tok_vals else None
        res.ttft_ms = sum(ttft_vals) / len(ttft_vals) if ttft_vals else None
        res.tok_s_min = min(tok_vals) if tok_vals else None
        res.tok_s_max = max(tok_vals) if tok_vals else None
        res.ttft_ms_min = min(ttft_vals) if ttft_vals else None
        res.ttft_ms_max = max(ttft_vals) if ttft_vals else None
        res.total_tokens = sum(o["total_tokens"] for o in outs)
        res.turns = int(round(sum(o["turns"] for o in outs) / len(outs)))
        seen: list[str] = []
        for o in outs:
            for name in o["tools_called"]:
                if name not in seen:
                    seen.append(name)
        res.tools_called = seen
        res.malformed_args = sum(o["malformed_args"] for o in outs)
        res.unknown_tools = sum(o["unknown_tools"] for o in outs)
        res.tool_errors = res.malformed_args + res.unknown_tools
        res.expected_tools = sum(
            len(sc.expected_tools)
            for sc, _, _ in groups
            if has_tools and sc.tools_enabled
        )
        res.scenario_runs = []
        for sc, sc_outs, sc_success in groups:
            stok = [o["tok_s"] for o in sc_outs if o["tok_s"] is not None]
            sttft = [o["ttft_ms"] for o in sc_outs if o["ttft_ms"] is not None]
            rated = [p for p in sc_success if p is not None]
            srate = sum(1 for p in rated if p) / len(rated) if rated else None
            sok = (srate == 1.0) if rated else None
            seen_sc: list[str] = []
            for o in sc_outs:
                for name in o["tools_called"]:
                    if name not in seen_sc:
                        seen_sc.append(name)
            res.scenario_runs.append(
                {
                    "scenario": sc.name,
                    "repeats": len(sc_outs),
                    "tok_s": sum(stok) / len(stok) if stok else None,
                    "ttft_ms": sum(sttft) / len(sttft) if sttft else None,
                    "turns": int(round(sum(o["turns"] for o in sc_outs) / len(sc_outs))),
                    "tools_called": seen_sc,
                    "success": sok,
                    "success_rate": srate,
                    "ok": _ok_str_for(sok, srate, len(sc_outs)),
                }
            )
        rated_all = [e for e in res.scenario_runs if e["success"] is not None]
        if rated_all:
            res.success_rate = sum(e["success_rate"] for e in rated_all) / len(rated_all)
            res.success = all(e["success"] for e in rated_all)
        res.runs = [
            {
                "scenario": sc.name,
                "repeat": i + 1,
                "tok_s": o["tok_s"],
                "ttft_ms": o["ttft_ms"],
                "turns": o["turns"],
                "total_tokens": o["total_tokens"],
                "tools_called": o["tools_called"],
                "success": p,
            }
            for sc, sc_outs, sc_success in groups
            for i, (o, p) in enumerate(zip(sc_outs, sc_success))
        ]
        finals = [o["final_answer"] for o in outs if o["final_answer"]]
        res.final_answer = finals[-1] if finals else None
        if len(scenarios) == 1 and scenarios[0].name == "weather":
            if res.success is not None:
                made = int(round(res.success_rate * args.repeat))
                if args.repeat > 1:
                    _say(
                        f"  correctness: {made}/{args.repeat} repeats passed "
                        f"({len(res.tools_called)}/{len(scenarios[0].expected_tools)} expected tools seen)",
                        "green" if res.success else "red",
                    )
                else:
                    _say(
                        f"  correctness: {'PASS' if res.success else 'FAIL'} "
                        f"({len(res.tools_called)}/{len(scenarios[0].expected_tools)} expected tools)",
                        "green" if res.success else "red",
                    )
        else:
            for sc, e in zip(scenarios, res.scenario_runs):
                if e["success"] is None:
                    _say(f"  [{sc.name}] correctness: n/a (unscored)", "dim")
                elif e["repeats"] > 1:
                    made = int(round(e["success_rate"] * e["repeats"]))
                    _say(
                        f"  [{sc.name}] correctness: {made}/{e['repeats']} repeats passed "
                        f"({len(e['tools_called'])}/{len(sc.expected_tools)} expected tools seen)",
                        "green" if e["success"] else "red",
                    )
                else:
                    _say(
                        f"  [{sc.name}] correctness: {'PASS' if e['success'] else 'FAIL'} "
                        f"({len(e['tools_called'])}/{len(sc.expected_tools)} expected tools)",
                        "green" if e["success"] else "red",
                    )
        if res.tool_errors:
            _say(
                f"  tool errors: {res.tool_errors} "
                f"(malformed args: {res.malformed_args}, "
                f"unknown tools: {res.unknown_tools})",
                "yellow",
            )
    except Exception as e:
        res.error = str(e)
        _say(f"  ERROR: {e}", "bold red")
    finally:
        res.total_s = time.perf_counter() - t_start
        if we_loaded and not args.no_unload:
            try:
                lm.unload(mid, instance_id)
                _say("  unloaded", "dim")
            except LMStudioError as e:
                _say(f"  unload failed: {e}", "yellow")
    return res


def _ok_str_for(success: bool | None, success_rate: float | None, repeats: int) -> str:
    if success is None:
        return "n/a"
    if repeats > 1 and success_rate is not None:
        return f"{int(round(success_rate * repeats))}/{repeats}"
    return "yes" if success else "no"


def ok_str(r: ModelResult) -> str:
    return _ok_str_for(r.success, r.success_rate, r.repeats)


def _scenario_names(results: list[ModelResult]) -> list[str]:
    names: list[str] = []
    for r in results:
        for e in r.scenario_runs:
            if e["scenario"] not in names:
                names.append(e["scenario"])
    return names


def _scenario_entry(r: ModelResult, name: str) -> dict | None:
    for e in r.scenario_runs:
        if e["scenario"] == name:
            return e
    return None


def _scenario_ok(r: ModelResult, name: str) -> str:
    e = _scenario_entry(r, name)
    return e["ok"] if e else "n/a"


def _scenario_cell(r: ModelResult, name: str) -> Text | str:
    if CONSOLE is None:
        return _scenario_ok(r, name)
    e = _scenario_entry(r, name)
    if e is None or e["success"] is None:
        return Text(e["ok"] if e else "n/a", style="dim")
    return Text(e["ok"], style="green" if e["success"] else "red")


def sort_results(results: list[ModelResult], sort_by: str) -> list[ModelResult]:
    if sort_by == "tok_s":
        return sorted(results, key=lambda r: (r.tok_s is None, -(r.tok_s or 0.0)))
    if sort_by == "ttft":
        return sorted(results, key=lambda r: (r.ttft_ms is None, r.ttft_ms or 0.0))
    if sort_by == "total":
        return sorted(results, key=lambda r: r.total_s)
    if sort_by == "load":
        return sorted(results, key=lambda r: (r.load_s is None, r.load_s or 0.0))
    if sort_by == "model":
        return sorted(results, key=lambda r: r.model)
    return list(results)


def summarize(results: list[ModelResult]) -> str | None:
    scored = [r for r in results if r.tok_s is not None and r.error is None]
    if not scored:
        return None
    fastest = max(scored, key=lambda r: r.tok_s or 0.0)
    parts = [f"fastest throughput: {fastest.model} ({fastest.tok_s:.1f} tok/s)"]
    ttfts = [r for r in scored if r.ttft_ms is not None]
    if ttfts:
        quickest = min(ttfts, key=lambda r: r.ttft_ms or float("inf"))
        parts.append(f"lowest TTFT: {quickest.model} ({quickest.ttft_ms:.0f} ms)")
    return "; ".join(parts)


def print_table(results: list[ModelResult], sort_by: str = "order") -> None:
    ordered = sort_results(results, sort_by)
    names = _scenario_names(results)
    multi = len(names) > 1
    if CONSOLE is None:
        _print_table_plain(ordered, names if multi else [])
    else:
        _print_table_rich(ordered, sort_by, names if multi else [])
    summary = summarize(results)
    if summary:
        _say(summary, "bold green")


def _ok_cell(r: ModelResult) -> Text | str:
    s = ok_str(r)
    if CONSOLE is None:
        return s
    if r.success is None:
        return Text(s, style="dim")
    return Text(s, style="green" if r.success else "red")


def _print_table_plain(ordered: list[ModelResult], names: list[str] | None = None) -> None:
    headers = [
        "Model",
        "Quant",
        "Rpt",
        "Load(s)",
        "TTFT(ms)",
        "tok/s",
        "Calls",
        "Turns",
        "OK",
        "Err",
        "Total(s)",
        "Error",
    ]
    names = names or []
    headers += [f"OK:{n}" for n in names]
    rows = []
    for r in ordered:
        rows.append(
            [
                r.model,
                r.quantization,
                str(r.repeats),
                f"{r.load_s:.1f}" if r.load_s is not None else "-",
                f"{r.ttft_ms:.0f}" if r.ttft_ms is not None else "-",
                f"{r.tok_s:.1f}" if r.tok_s is not None else "-",
                f"{len(r.tools_called)}/{r.expected_tools}"
                if r.expected_tools
                else "-",
                str(r.turns),
                ok_str(r),
                str(r.tool_errors) if r.tool_errors else "-",
                f"{r.total_s:.1f}",
                (r.error or "")[:40],
            ]
            + [_scenario_ok(r, n) for n in names]
        )
    widths = [
        max(len(h), *(len(row[i]) for row in rows)) if rows else len(h)
        for i, h in enumerate(headers)
    ]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print("\n" + line)
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def _print_table_rich(
    ordered: list[ModelResult], sort_by: str, names: list[str] | None = None
) -> None:
    assert CONSOLE is not None
    names = names or []
    best_tok = max([r.tok_s or 0.0 for r in ordered], default=0.0)
    ttfts = [r.ttft_ms for r in ordered if r.ttft_ms is not None]
    best_ttft = min(ttfts, default=None)
    table = Table(
        title=f"Benchmark results ({len(ordered)} model(s), sorted by {sort_by})",
        header_style="bold cyan",
        border_style="dim",
    )
    for name, justify in [
        ("Model", "left"),
        ("Quant", "left"),
        ("Rpt", "right"),
        ("Load(s)", "right"),
        ("TTFT(ms)", "right"),
        ("tok/s", "right"),
        ("Calls", "right"),
        ("Turns", "right"),
        ("OK", "center"),
        ("Err", "center"),
        ("Total(s)", "right"),
        ("Error", "left"),
    ]:
        table.add_column(name, justify=justify, overflow="fold")
    for n in names:
        table.add_column(f"OK:{n}", justify="center")
    for r in ordered:
        tok = f"{r.tok_s:.1f}" if r.tok_s is not None else "-"
        ttft = f"{r.ttft_ms:.0f}" if r.ttft_ms is not None else "-"
        table.add_row(
            Text(r.model),
            Text(r.quantization, style="dim"),
            str(r.repeats),
            f"{r.load_s:.1f}" if r.load_s is not None else "-",
            Text(
                ttft,
                style="bold green"
                if best_ttft is not None and r.ttft_ms == best_ttft
                else ("dim" if ttft == "-" else ""),
            ),
            Text(
                tok,
                style="bold green"
                if best_tok > 0 and r.tok_s == best_tok
                else ("dim" if tok == "-" else ""),
            ),
            f"{len(r.tools_called)}/{r.expected_tools}"
            if r.expected_tools
            else "-",
            str(r.turns),
            _ok_cell(r),
            Text(str(r.tool_errors), style="red")
            if r.tool_errors
            else Text("-", style="dim"),
            f"{r.total_s:.1f}",
            Text((r.error or "")[:40], style="red") if r.error else "",
            *[_scenario_cell(r, n) for n in names],
        )
    CONSOLE.print()
    CONSOLE.print(table)


def write_csv(results: list[ModelResult], path: str) -> None:
    fields = [
        "model",
        "quantization",
        "tool_use",
        "repeats",
        "load_s",
        "warmup_s",
        "ttft_ms",
        "ttft_ms_min",
        "ttft_ms_max",
        "tok_s",
        "tok_s_min",
        "tok_s_max",
        "total_tokens",
        "turns",
        "tools_called",
        "expected_tools",
        "success",
        "success_rate",
        "tool_errors",
        "malformed_args",
        "unknown_tools",
        "total_s",
        "error",
        "final_answer",
        "scenario_scores",
    ]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for r in results:
            d = asdict(r)
            d["tools_called"] = ";".join(r.tools_called)
            d["scenario_scores"] = ";".join(
                f"{e['scenario']}={e['ok']}" for e in r.scenario_runs
            )
            w.writerow([d.get(k) for k in fields])
    _say(f"\ncsv written to {path}", "green")


def _md_cell(s: object) -> str:
    return str(s).replace("|", "/")


def write_markdown(results: list[ModelResult], path: str) -> None:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        "# LM Studio agent bench",
        "",
        f"Generated {stamp}; {len(results)} model(s).",
        "",
        "| Model | tok/s | TTFT(ms) | Turns | OK | Err | Total(s) | Error |",
        "| --- | ---: | ---: | ---: | :---: | ---: | ---: | --- |",
    ]
    for r in sort_results(results, "tok_s"):
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {:.1f} | {} |".format(
                _md_cell(r.model),
                f"{r.tok_s:.1f}" if r.tok_s is not None else "-",
                f"{r.ttft_ms:.0f}" if r.ttft_ms is not None else "-",
                r.turns,
                ok_str(r),
                r.tool_errors if r.tool_errors else "-",
                r.total_s,
                _md_cell((r.error or "")[:40]),
            )
        )
    multi = [r for r in results if len(r.scenario_runs) > 1]
    if multi:
        lines += ["", "Per-scenario pass rates:"]
        for r in sort_results(multi, "tok_s"):
            lines.append(
                "- {}: {}".format(
                    _md_cell(r.model),
                    ", ".join(f"{e['scenario']} {e['ok']}" for e in r.scenario_runs),
                )
            )
    summary = summarize(results)
    if summary:
        lines += ["", summary]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nmarkdown written to {path}")


def load_baseline(path: str) -> dict[str, dict]:
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        die(f"cannot read baseline {path!r}: {e}")
    if isinstance(data, dict) and "results" in data:
        data = data["results"]
    if not isinstance(data, list):
        die(f"baseline {path!r} is not a JSON list of results")
    return {r["model"]: r for r in data if isinstance(r, dict) and r.get("model")}


def fmt_delta(cur: float | None, prev: float | None, invert: bool = False) -> str:
    if cur is None or prev is None or prev == 0:
        return "-"
    pct = (cur - prev) / abs(prev) * 100
    if abs(pct) < 0.05:
        return "+0.0%"
    mark = ""
    if abs(pct) >= 0.5:
        mark = " \u25b2" if (pct > 0) != invert else " \u25bc"
    return f"{'+' if pct >= 0 else ''}{pct:.1f}%{mark}"


def print_baseline_comparison(
    results: list[ModelResult], baseline: dict[str, dict]
) -> None:
    current = {r.model: r for r in results}
    if CONSOLE is None:
        _print_baseline_plain(current, baseline)
    else:
        _print_baseline_rich(current, baseline)
    for model in baseline:
        if model not in current:
            _say(f"  dropped since baseline: {model}", "yellow")


def _delta_cell(
    cur: float | None, prev: float | None, invert: bool = False
) -> Text | str:
    s = fmt_delta(cur, prev, invert=invert)
    if CONSOLE is None:
        return s
    if s == "-":
        return Text(s, style="dim")
    if "▲" in s:
        return Text(s, style="green" if not invert else "red")
    if "▼" in s:
        return Text(s, style="red" if not invert else "green")
    return Text(s)


def _print_baseline_plain(
    current: dict[str, ModelResult], baseline: dict[str, dict]
) -> None:
    headers = ["Model", "Verdict", "tok/s Δ", "TTFT Δ"]
    rows = []
    for model, r in current.items():
        prev = baseline.get(model)
        if prev is None:
            rows.append([model, "new", "-", "-"])
            continue
        pv, cv = prev.get("success"), r.success
        if pv is None or cv is None:
            verdict = "-"
        elif pv == cv:
            verdict = "same"
        else:
            verdict = f"{'PASS' if pv else 'FAIL'}->{'PASS' if cv else 'FAIL'}"
        rows.append(
            [
                model,
                verdict,
                fmt_delta(r.tok_s, prev.get("tok_s")),
                fmt_delta(r.ttft_ms, prev.get("ttft_ms"), invert=True),
            ]
        )
    widths = [
        max(len(h), *(len(row[i]) for row in rows)) if rows else len(h)
        for i, h in enumerate(headers)
    ]
    print("\nvs baseline:")
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def _print_baseline_rich(
    current: dict[str, ModelResult], baseline: dict[str, dict]
) -> None:
    assert CONSOLE is not None
    table = Table(title="vs baseline", header_style="bold cyan", border_style="dim")
    table.add_column("Model", overflow="fold")
    table.add_column("Verdict", justify="center")
    table.add_column("tok/s Δ", justify="right")
    table.add_column("TTFT Δ", justify="right")
    for model, r in current.items():
        prev = baseline.get(model)
        if prev is None:
            table.add_row(Text(model), Text("new", style="blue"), "-", "-")
            continue
        pv, cv = prev.get("success"), r.success
        if pv is None or cv is None:
            verdict: Text | str = Text("-", style="dim")
        elif pv == cv:
            verdict = Text("same", style="dim")
        else:
            verdict = Text(
                f"{'PASS' if pv else 'FAIL'}->{'PASS' if cv else 'FAIL'}",
                style="green" if cv else "red",
            )
        table.add_row(
            Text(model),
            verdict,
            _delta_cell(r.tok_s, prev.get("tok_s")),
            _delta_cell(r.ttft_ms, prev.get("ttft_ms"), invert=True),
        )
    CONSOLE.print()
    CONSOLE.print(table)


INDEX_PART_RE = re.compile(r"^(\d+)(?:-(\d+))?$")


def parse_index_spec(spec: str, count: int) -> list[int]:
    """Expand '1-3, 5, 7-10' into sorted 1-based indices; raises ValueError."""
    idxs: list[int] = []
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        m = INDEX_PART_RE.fullmatch(part)
        if m is None:
            raise ValueError(f"{part!r} is not a number or range (e.g. 1-3, 5, 7-10)")
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        if lo < 1 or hi < 1:
            raise ValueError(f"{part!r} is out of range (valid: 1-{count})")
        if lo > hi:
            lo, hi = hi, lo
        idxs.extend(range(lo, hi + 1))
    if not idxs:
        raise ValueError("no selection given")
    bad = sorted({i for i in idxs if not 1 <= i <= count})
    if bad:
        shown = ", ".join(str(b) for b in bad)
        raise ValueError(f"out of range: {shown} (valid: 1-{count})")
    return sorted(set(idxs))


def looks_like_index_spec(part: str) -> bool:
    return INDEX_PART_RE.fullmatch(part) is not None


def select_models(models: list[dict], args: argparse.Namespace) -> list[dict]:
    if args.all:
        return list(models)
    if args.models:
        picked: list[dict] = []
        for part in args.models.split(","):
            part = part.strip()
            if not part:
                continue
            exact = [m for m in models if m["id"] == part]
            if exact:
                m = exact[0]
            elif looks_like_index_spec(part):
                try:
                    nums = parse_index_spec(part, len(models))
                except ValueError as e:
                    die(f"--models {part!r}: {e}")
                for n in nums:
                    m = models[n - 1]
                    if m not in picked:
                        picked.append(m)
                continue
            else:
                matches = [m for m in models if part.lower() in m["id"].lower()]
                if len(matches) == 1:
                    m = matches[0]
                elif not matches:
                    die(f"no model matching {part!r}")
                else:
                    die(
                        f"ambiguous model {part!r}: "
                        + ", ".join(x["id"] for x in matches)
                    )
            if m not in picked:
                picked.append(m)
        if not picked:
            die("no models selected")
        return picked
    if not sys.stdin.isatty():
        die(
            "no selection given; pass --all or --models id1,id2 (ids or list "
            "indices like 1-3,5)"
        )
    if CONSOLE is not None:
        table = _model_table(numbered=True)
        for i, m in enumerate(models, 1):
            _add_model_row(table, i, m)
        CONSOLE.print(f"Chat models on {args.host}:{args.port}:")
        CONSOLE.print()
        CONSOLE.print(table)
    else:
        print(f"Chat models on {args.host}:{args.port}:\n")
        for i, m in enumerate(models, 1):
            caps = ",".join(m.get("capabilities") or []) or "-"
            print(
                f"  {i:2d}. {m['id']}  [{m.get('quantization') or '?'}] {_size_str(m['id'])} "
                f"{m.get('state', '?')}  caps: {caps}"
            )
    while True:
        try:
            if CONSOLE is not None:
                choice = Prompt.ask(
                    "Select models to test (numbers/ranges, 'all', or 'q' to quit)"
                ).strip()
            else:
                choice = input(
                    "\nSelect models to test (numbers/ranges, 'all', or 'q' to quit): "
                ).strip()
        except EOFError:
            die("no selection")
        if choice.lower() in ("q", "quit"):
            _say("bye", "dim")
            sys.exit(0)
        if choice.lower() == "all":
            return list(models)
        try:
            idxs = parse_index_spec(choice, len(models))
        except ValueError as e:
            _say(f"{e}", "yellow")
            continue
        return [models[i - 1] for i in idxs]


def die(msg: str) -> None:
    if CONSOLE is not None:
        Console(stderr=True).print(f"error: {msg}", style="bold red", markup=False)
    else:
        print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _result_from_dict(d: dict) -> ModelResult:
    known = {f.name for f in fields(ModelResult)}
    return ModelResult(**{k: v for k, v in d.items() if k in known})


def _load_result_file(path: str) -> list[ModelResult]:
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        die(f"cannot read results {path!r}: {e}")
    if isinstance(data, dict) and "results" in data:
        data = data["results"]
    if (
        not isinstance(data, list)
        or not data
        or not all(isinstance(r, dict) and r.get("model") for r in data)
    ):
        die(f"results file {path!r} is not a JSON list of benchmark results")
    return [_result_from_dict(r) for r in data]


def _show_index(entries: list[tuple[str, list[ModelResult]]]) -> None:
    rows = []
    for path, results in entries:
        scored = [r for r in results if r.tok_s is not None]
        best = max(scored, key=lambda r: r.tok_s or 0.0) if scored else None
        rows.append(
            [
                os.path.basename(path),
                datetime.fromtimestamp(os.path.getmtime(path)).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                str(len(results)),
                f"{best.model} ({best.tok_s:.1f})" if best else "-",
            ]
        )
    if CONSOLE is not None:
        table = Table(title="Saved runs", header_style="bold cyan", border_style="dim")
        table.add_column("File", overflow="fold")
        table.add_column("Saved")
        table.add_column("Models", justify="right")
        table.add_column("Best tok/s")
        for row in rows:
            table.add_row(Text(row[0]), row[1], row[2], row[3])
        CONSOLE.print(table)
    else:
        headers = ["File", "Saved", "Models", "Best tok/s"]
        widths = [
            max(len(h), *(len(row[i]) for row in rows)) if rows else len(h)
            for i, h in enumerate(headers)
        ]
        print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
        print("  ".join("-" * w for w in widths))
        for row in rows:
            print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def show_results(path: str, sort_by: str) -> int:
    if os.path.isdir(path):
        files = sorted(
            (
                os.path.join(path, name)
                for name in os.listdir(path)
                if name.endswith(".json")
            ),
            key=os.path.getmtime,
        )
        if not files:
            die(f"no result files in {path!r}")
        entries = [(f, _load_result_file(f)) for f in files]
        _show_index(entries)
        for f, results in entries:
            _rule(f)
            print_table(results, sort_by)
        return 0
    if not os.path.isfile(path):
        die(f"no such results file {path!r}")
    _rule(path)
    print_table(_load_result_file(path), sort_by)
    return 0

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark agentic (tool-calling) speed of LM Studio models.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--api-key", default=None, help="LM Studio API key, if the server requires one"
    )
    parser.add_argument(
        "--models",
        help=(
            "models to test: comma-separated ids (exact or substring) and/or "
            "list indices/ranges (e.g. '1-3,5,7-10' from the numbered list)"
        ),
    )
    parser.add_argument("--all", action="store_true", help="test every chat model")
    parser.add_argument(
        "--task",
        default=DEFAULT_TASK,
        help="override the agentic task prompt (disables correctness scoring)",
    )
    parser.add_argument(
        "--max-turns", type=int, default=6, help="max tool-calling round trips"
    )
    parser.add_argument(
        "--max-tokens", type=int, default=1024, help="max tokens per turn"
    )
    parser.add_argument(
        "--timeout", type=int, default=300, help="per-request timeout (s)"
    )
    parser.add_argument(
        "--load-timeout",
        type=int,
        default=600,
        help="max seconds to wait for a model to load",
    )
    parser.add_argument(
        "--no-warmup", action="store_true", help="skip the warmup request after loading"
    )
    parser.add_argument(
        "--no-unload", action="store_true", help="leave models loaded after testing"
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run each model N times; speed metrics are averaged",
    )
    parser.add_argument(
        "--sort-by",
        choices=["order", "tok_s", "ttft", "total", "load", "model"],
        default="order",
        help="sort the comparison table",
    )
    parser.add_argument(
        "--require-tool-use",
        action="store_true",
        help="only test models advertising the tool_use capability",
    )
    parser.add_argument(
        "--exclude",
        default=None,
        help="comma-separated substrings; skip matching model ids",
    )
    parser.add_argument(
        "--list", action="store_true", help="list chat models and exit"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print tool arguments and results each turn",
    )
    parser.add_argument(
        "--transcript-dir",
        metavar="DIR",
        default=None,
        help="save per-repeat message transcripts as JSON in DIR",
    )
    parser.add_argument("--json", metavar="PATH", help="write results to a JSON file")
    parser.add_argument("--csv", metavar="PATH", help="write results to a CSV file")
    parser.add_argument(
        "--markdown", metavar="PATH", help="write results to a Markdown report"
    )
    parser.add_argument(
        "--baseline",
        metavar="PATH",
        help="compare against a previous --json run",
    )
    parser.add_argument(
        "--scenarios",
        default="weather",
        help="comma-separated benchmark scenarios (choose from: weather,agent-code,codegen)",
    )
    parser.add_argument(
        "--show",
        metavar="PATH",
        default=None,
        help="view saved --json run(s) in the TUI without contacting the server (file or directory)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable rich colors",
    )
    args = parser.parse_args(argv)

    if args.no_color and CONSOLE is not None:
        CONSOLE.no_color = True

    if args.show:
        return show_results(args.show, args.sort_by)

    scenarios = resolve_scenarios(args.scenarios)
    if args.task != DEFAULT_TASK and [s.name for s in scenarios] != ["weather"]:
        die("--task overrides the task text, so it only works with the weather scenario")

    if args.repeat < 1:
        die("--repeat must be >= 1")
    if args.max_turns < 1:
        die("--max-turns must be >= 1")
    if args.max_tokens < 1:
        die("--max-tokens must be >= 1")
    if args.timeout <= 0 or args.load_timeout <= 0:
        die("--timeout and --load-timeout must be > 0")

    lm = LMStudio(args.host, args.port, args.api_key, args.timeout)
    try:
        models = lm.list_models()
    except LMStudioError as e:
        die(str(e))
    chat_models = [m for m in models if is_chat_model(m)]
    skipped = [m["id"] for m in models if not is_chat_model(m)]
    if skipped:
        _say(
            f"  excluding {len(skipped)} embedding model(s): {', '.join(skipped)}",
            "dim",
        )
    if not chat_models:
        die("no chat models found on the server")
    if args.require_tool_use:
        chat_models = [
            m for m in chat_models if "tool_use" in (m.get("capabilities") or [])
        ]
    if args.exclude:
        parts = [p.strip().lower() for p in args.exclude.split(",") if p.strip()]
        chat_models = [
            m for m in chat_models if not any(p in m["id"].lower() for p in parts)
        ]
    chat_models = sort_models_by_size(chat_models)
    if not chat_models:
        die("no chat models left after filtering")
    if args.list:
        if CONSOLE is not None:
            table = _model_table()
            for m in chat_models:
                _add_model_row(table, None, m)
            CONSOLE.print(table)
        else:
            for m in chat_models:
                caps = ",".join(m.get("capabilities") or []) or "-"
                print(
                    f"  {m['id']}  [{m.get('quantization') or '?'}] {_size_str(m['id'])} "
                    f"{m.get('state', '?')}  caps: {caps}"
                )
        return 0

    selection = select_models(chat_models, args)
    results: list[ModelResult] = []
    try:
        for i, m in enumerate(selection, 1):
            results.append(bench_one(lm, m, args, i, len(selection), scenarios))
    except KeyboardInterrupt:
        _say("\ninterrupted", "yellow")

    if results:
        print_table(results, args.sort_by)
    if args.json and results:
        with open(args.json, "w") as f:
            json.dump([asdict(r) for r in results], f, indent=2)
        _say(f"\nresults written to {args.json}", "green")
    if args.csv and results:
        write_csv(results, args.csv)
    if args.markdown and results:
        write_markdown(results, args.markdown)
    if args.baseline and results:
        print_baseline_comparison(results, load_baseline(args.baseline))
    return 0 if results and all(r.error is None for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
