"""Fibonacci code extraction and deterministic checker execution."""

from __future__ import annotations

import re

from .base import Scenario, run_python

CHECKER = """import runpy
fib = runpy.run_path('solution.py')['fib']
assert fib(0) == 0
assert fib(1) == 1
assert [fib(i) for i in range(10)] == [0,1,1,2,3,5,8,13,21,34]
assert fib(20) == 6765
assert all(type(fib(i)) is int for i in range(10))
print('all tests passed')
"""


def extract_code(answer: str) -> str:
    block = re.search(r"```[^\n]*\n(.*?)```", answer, re.DOTALL)
    return (block.group(1) if block else answer).strip()


class Codegen(Scenario):
    """Score an extracted Fibonacci implementation using an isolated subprocess."""

    name = "codegen"
    prompt = (
        "Return one fenced python code block defining fib(n), returning the nth Fibonacci "
        "number as an int. fib(0)=0, fib(1)=1. No other text."
    )

    async def score(self, answer: str, called: list[str]) -> tuple[bool | None, str]:
        (self.root / "solution.py").write_text(extract_code(answer), encoding="utf-8")
        result = await run_python(self.root, CHECKER)
        completed = str(result["output"]).rstrip().endswith("all tests passed")
        return bool(result["passed"]) and completed, str(result["output"])
