"""Task workspaces and controller-side scoring for built-in suites."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from typing import Any

from llmsweep.benchmarks.corpora.constraint import exhaustive, grade_assignment
from llmsweep.benchmarks.registry import find_task
from llmsweep.benchmarks.types import TaskSpec
from llmsweep.packs import grade_pack_task
from llmsweep.scenarios.base import Scenario, run_python, tool

_FENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_code(answer: str) -> str:
    match = _FENCE.search(answer)
    return (match.group(1) if match else answer).strip()


def _checker_script(function: str, tests: list[list[Any]]) -> str:
    payload = json.dumps(tests, ensure_ascii=False)
    return f"""import json, runpy
fn = runpy.run_path('solution.py')[{function!r}]
cases = json.loads({payload!r})
failures = []
for index, (args, expected) in enumerate(cases, 1):
    try:
        if isinstance(expected, dict) and expected.get('raises'):
            try:
                fn(*args)
            except Exception as exc:
                if type(exc).__name__ != expected['raises']:
                    failures.append(f'{{index}}: raised {{type(exc).__name__}}')
            else:
                failures.append(f'{{index}}: expected {{expected["raises"]}}')
            continue
        got = fn(*args)
        if got != expected:
            failures.append(f'{{index}}: got {{got!r}} expected {{expected!r}}')
    except Exception as exc:
        failures.append(f'{{index}}: {{type(exc).__name__}}: {{exc}}')
if failures:
    print('\\n'.join(failures))
    raise SystemExit(1)
print('all tests passed')
"""


async def score_code(
    root: Any, answer: str, payload: dict[str, Any]
) -> tuple[bool, str, list[dict[str, Any]]]:
    (root / "solution.py").write_text(extract_code(answer), encoding="utf-8")
    result = await run_python(
        root, _checker_script(str(payload["function"]), list(payload["tests"]))
    )
    output = str(result["output"])
    passed = bool(result["passed"]) and output.rstrip().endswith("all tests passed")
    assertions = [
        {"name": f"case-{index}", "passed": passed and not output}
        for index, _case in enumerate(payload["tests"], 1)
    ]
    if not passed:
        assertions = [{"name": "hidden-cases", "passed": False, "detail": output[-500:]}]
    return passed, output, assertions


def grade_knowledge(
    answer: str, payload: dict[str, Any]
) -> tuple[bool | None, str, str | None, dict[str, Any]]:
    try:
        parsed = json.loads(answer[answer.find("{") : answer.rfind("}") + 1] or answer)
    except (ValueError, TypeError):
        return False, "invalid output", "invalid_output", {"brier": None}
    if not isinstance(parsed, dict):
        return False, "invalid output", "invalid_output", {"brier": None}
    abstain = parsed.get("abstain")
    confidence = parsed.get("confidence")
    text = str(parsed.get("answer", "")).strip()
    if (
        not isinstance(abstain, bool)
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
    ):
        return False, "invalid output", "invalid_output", {"brier": None}
    if not math_finite(float(confidence)) or not 0 <= float(confidence) <= 1:
        return False, "invalid output", "invalid_output", {"brier": None}
    expected_abstain = bool(payload["abstain"])
    if expected_abstain:
        success = abstain is True
        return (
            success,
            "abstention" if success else "failed to abstain",
            None if success else "incorrect",
            {
                "brier": None,
                "abstain": True,
            },
        )
    if abstain:
        return (
            False,
            "abstained on an answerable item",
            "incorrect",
            {"brier": None, "abstain": True},
        )
    aliases = {item.casefold() for item in payload["aliases"]}
    success = text.casefold() in aliases
    brier = (float(confidence) - (1.0 if success else 0.0)) ** 2
    category = None if success else "incorrect"
    return success, text, category, {"brier": brier, "abstain": False}


def math_finite(value: float) -> bool:
    return value == value and value not in {float("inf"), float("-inf")}


class TaskScenario(Scenario):
    """Fresh workspace for one registry task."""

    def __init__(self, spec: TaskSpec) -> None:
        super().__init__()
        self.spec = spec
        self.name = spec.suite_id
        self.prompt = spec.prompt
        self.max_turns = spec.max_turns
        self.expected_tools = spec.tools
        self._hashes = self._materialize()
        # Per-task tool schemas shadow the shared class list without mutating it.
        self.__dict__["tools"] = _tools_for(spec)

    def _materialize(self) -> dict[str, str]:
        files = self.spec.payload.get("files", {})
        hashes: dict[str, str] = {}
        for name, content in files.items():
            path = self.path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(content), encoding="utf-8")
            if name in self.spec.payload.get("protected", []):
                hashes[name] = hashlib.sha256(str(content).encode()).hexdigest()
        return hashes

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "list_files":
                return {
                    "files": sorted(
                        p.relative_to(self.root).as_posix()
                        for p in self.root.rglob("*")
                        if p.is_file()
                    )
                }
            if name == "read_file":
                file_path = self.path(str(args["path"]))
                if file_path.is_symlink():
                    return {"error": "symlink reads are not allowed"}
                text = file_path.read_text(encoding="utf-8")[:4001]
                return {"content": text[:4000], "truncated": len(text) > 4000}
            if name == "grep":
                pattern = re.compile(str(args["pattern"]))
                matches: list[dict[str, Any]] = []
                for file_path in sorted(self.root.rglob("*")):
                    if not file_path.is_file() or file_path.is_symlink():
                        continue
                    for number, line in enumerate(
                        file_path.read_text(encoding="utf-8").splitlines(), 1
                    ):
                        if pattern.search(line):
                            matches.append(
                                {"path": file_path.name, "line": number, "text": line[:500]}
                            )
                            if len(matches) >= 50:
                                return {"matches": matches, "truncated": True}
                return {"matches": matches, "truncated": False}
            if name == "write_file":
                file_path = self.path(str(args["path"]))
                relative = file_path.relative_to(self.root).as_posix()
                allowed = set(self.spec.payload.get("writable", []))
                if relative not in allowed:
                    return {"error": f"tool policy: {relative} is not writable"}
                if not isinstance(args.get("content"), str):
                    raise ValueError("content must be a string")
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(args["content"], encoding="utf-8")
                return {"written": relative}
            if name == "run_public_tests":
                return await self._run_script(
                    str(self.spec.payload.get("public_test", "print('public ok')"))
                )
        except (KeyError, ValueError, TypeError, OSError, re.error) as exc:
            return {"error": f"{name}: {exc}"}
        return await super().call(name, args)

    async def _run_script(self, script: str) -> dict[str, Any]:
        # -I drops the workspace from sys.path; put it back without loading user site packages.
        wrapped = "import sys\nsys.path.insert(0, '.')\n" + script
        result = await run_python(self.root, wrapped, deadline=15)
        return {"passed": bool(result["passed"]), "output": str(result["output"])[-500:]}

    async def score(self, answer: str, called: list[str]) -> tuple[bool | None, str]:
        detail = await self.score_detail(answer, called)
        return detail[0], detail[1]

    async def score_detail(
        self, answer: str, called: list[str]
    ) -> tuple[bool | None, str, str | None, list[dict[str, Any]], dict[str, Any]]:
        suite = self.spec.suite_id
        if suite == "code-edge":
            passed, output, assertions = await score_code(self.root, answer, self.spec.payload)
            category = None if passed else "incorrect"
            return passed, output, category, assertions, {}
        if suite == "constraint-plan":
            return _score_constraint(answer, self.spec.payload)
        if suite == "knowledge-cal":
            success, output, category, metrics = grade_knowledge(answer, self.spec.payload)
            return success, output, category, [], metrics
        return await self._score_repo(called)

    async def _score_repo(
        self, called: list[str]
    ) -> tuple[bool | None, str, str | None, list[dict[str, Any]], dict[str, Any]]:
        violations = []
        for name, digest in self._hashes.items():
            file_path = self.path(name)
            if (
                not file_path.is_file()
                or hashlib.sha256(file_path.read_bytes()).hexdigest() != digest
            ):
                violations.append(name)
        if violations:
            return False, "protected files changed: " + ",".join(violations), "tool_policy", [], {}
        hidden = str(self.spec.payload["hidden_test"])
        result = await self._run_script(hidden)
        passed = bool(result["passed"]) and str(result["output"]).rstrip().endswith(
            "all tests passed"
        )
        if self.spec.suite_id == "multi-file" and "write_file" not in called:
            passed = False
        category = None if passed else "incorrect"
        return passed, str(result["output"]), category, [{"name": "hidden", "passed": passed}], {}

    def diff_text(self) -> str:
        lines: list[str] = []
        for name, content in self.spec.payload.get("files", {}).items():
            file_path = self.root / name
            current = file_path.read_text(encoding="utf-8") if file_path.is_file() else ""
            if current == content:
                continue
            lines.extend(
                difflib.unified_diff(
                    str(content).splitlines(),
                    current.splitlines(),
                    fromfile=f"a/{name}",
                    tofile=f"b/{name}",
                    lineterm="",
                )
            )
        text = "\n".join(lines)
        return text[:8000]


def _score_constraint(
    answer: str, payload: dict[str, Any]
) -> tuple[bool | None, str, str | None, list[dict[str, Any]], dict[str, Any]]:
    try:
        parsed = json.loads(answer[answer.find("{") : answer.rfind("}") + 1])
    except (ValueError, TypeError):
        return False, "invalid output", "invalid_output", [], {}
    if not isinstance(parsed, dict):
        return False, "invalid output", "invalid_output", [], {}
    problem = payload["problem"]
    passed, output = grade_assignment(problem, parsed)
    oracle = exhaustive(problem)
    metrics = {"oracle_feasible": oracle}
    category = None if passed else "invalid_output" if output.startswith("invalid") else "incorrect"
    return (
        passed,
        output,
        category,
        [{"name": "constraints", "passed": passed, "detail": output}],
        metrics,
    )


def _tools_for(spec: TaskSpec) -> list[dict[str, Any]]:
    if not spec.tools:
        return []
    catalog = {
        "list_files": tool("list_files", "List workspace files", {}),
        "read_file": tool("read_file", "Read a workspace file", {"path": {"type": "string"}}),
        "grep": tool(
            "grep",
            "Search workspace files",
            {"pattern": {"type": "string"}, "path": {"type": "string"}},
            ["pattern"],
        ),
        "write_file": tool(
            "write_file",
            "Write an allowed file",
            {"path": {"type": "string"}, "content": {"type": "string"}},
        ),
        "run_public_tests": tool("run_public_tests", "Run the public tests only", {}),
    }
    return [catalog[name] for name in spec.tools if name in catalog]


class PackTask(Scenario):
    """Exact, contains, or regex task loaded from a local pack."""

    def __init__(self, spec: TaskSpec) -> None:
        super().__init__()
        self.spec = spec
        self.name = spec.suite_id
        self.prompt = spec.prompt
        self.max_turns = spec.max_turns

    async def score(self, answer: str, called: list[str]) -> tuple[bool | None, str]:
        passed, output, _category = grade_pack_task(answer, self.spec.payload)
        return passed, output

    async def score_detail(
        self, answer: str, called: list[str]
    ) -> tuple[bool | None, str, str | None, list[dict[str, Any]], dict[str, Any]]:
        passed, output, category = grade_pack_task(answer, self.spec.payload)
        return passed, output, category, [{"name": self.spec.evaluator_id, "passed": passed}], {}


def open_spec(spec: TaskSpec, *, custom: str | None = None) -> Scenario:
    if spec.pack_id == "builtin" and spec.suite_id in {"weather", "agent-code", "codegen"}:
        from llmsweep.scenarios import create_scenario

        return create_scenario(spec.suite_id, custom if spec.suite_id == "weather" else None)
    if spec.evaluator_id in {"exact", "contains", "regex"}:
        return PackTask(spec)
    return TaskScenario(spec)


def open_task(suite_id: str, task_id: str, *, custom: str | None = None) -> Scenario:
    if suite_id in {"weather", "agent-code", "codegen"}:
        from llmsweep.scenarios import create_scenario

        return create_scenario(suite_id, custom if suite_id == "weather" else None)
    return TaskScenario(find_task(suite_id, task_id))
