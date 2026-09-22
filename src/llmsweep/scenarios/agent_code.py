"""Constrained file tools and independent regression tests for a summation bug."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from .base import Scenario, run_python, tool

BUGGY = """def total(items):
    value = 0
    for i in range(1, len(items)):
        value += items[i]
    return value
"""
TESTS = """from buggy import total
assert total([1, 2, 3]) == 6
assert total([]) == 0
assert total([5]) == 5
assert total([-1, 1, 0]) == 0
assert total(range(100)) == sum(range(100))
print('all tests passed')
"""


class AgentCode(Scenario):
    """Repair a single writable Python module and rerun canonical assertions."""

    name = "agent-code"
    max_turns = 10
    expected_tools = ("write_file",)
    prompt = (
        "Inspect this workspace and fix buggy.py so total(items) passes tests.py. "
        "Use the tools to edit the file and run the tests."
    )
    tools: ClassVar[list[dict[str, Any]]] = [
        tool("list_files", "List workspace files", {}),
        tool("read_file", "Read up to 4000 characters", {"path": {"type": "string"}}),
        tool(
            "grep",
            "Find regex matches, capped at 50",
            {"pattern": {"type": "string"}, "path": {"type": "string"}},
            ["pattern"],
        ),
        tool(
            "write_file",
            "Write buggy.py; all other files are read-only",
            {"path": {"type": "string"}, "content": {"type": "string"}},
        ),
        tool("run_tests", "Run the authoritative tests", {}),
    ]

    def __init__(self) -> None:
        super().__init__()
        (self.root / "README.md").write_text(
            "total(items) should sum all items, but currently skips the first. Fix buggy.py.\n"
        )
        (self.root / "buggy.py").write_text(BUGGY)
        (self.root / "tests.py").write_text(TESTS)

    async def run_tests(self) -> dict[str, Any]:
        # Recreate the checker: previous generated code may have changed workspace files.
        tests = self.root / "tests.py"
        if tests.is_symlink():
            tests.unlink()
        tests.write_text(TESTS)
        result = await run_python(
            self.root,
            "import sys, runpy; sys.path.insert(0, '.'); "
            "runpy.run_path('tests.py', run_name='__main__')",
        )
        result["passed"] = bool(result["passed"]) and str(result["output"]).rstrip().endswith(
            "all tests passed"
        )
        return result

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "list_files":
                return {"files": sorted(p.name for p in self.root.iterdir() if p.is_file())}
            if name == "run_tests":
                return await self.run_tests()
            if name == "read_file":
                with self.path(args["path"]).open(encoding="utf-8") as handle:
                    content = handle.read(4001)
                return {"content": content[:4000], "truncated": len(content) > 4000}
            if name == "write_file":
                path = self.path(args["path"])
                if path != self.root / "buggy.py":
                    return {"error": "read-only path; writable set: buggy.py"}
                if not isinstance(args["content"], str):
                    raise ValueError("content must be a string")
                path.write_text(args["content"], encoding="utf-8")
                return {"written": "buggy.py"}
            if name == "grep":
                pattern = re.compile(args["pattern"])
                paths = [self.path(args["path"])] if "path" in args else sorted(self.root.iterdir())
                matches = []
                for path in paths:
                    path = self.path(path.relative_to(self.root).as_posix())
                    if not path.is_file():
                        continue
                    with path.open(encoding="utf-8") as handle:
                        for number, line in enumerate(handle, 1):
                            if pattern.search(line):
                                matches.append(
                                    {
                                        "path": path.name,
                                        "line": number,
                                        "text": line.rstrip()[:4000],
                                    }
                                )
                                if len(matches) > 50:
                                    return {"matches": matches[:50], "truncated": True}
                return {"matches": matches, "truncated": False}
        except (KeyError, ValueError, TypeError, OSError, re.error) as exc:
            return {"error": f"{name}: {exc}"}
        return await super().call(name, args)

    async def score(self, answer: str, called: list[str]) -> tuple[bool | None, str]:
        result = await self.run_tests()
        return "write_file" in called and bool(result["passed"]), str(result["output"])
