from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llmsweep.scenarios.agent_code import AgentCode
from llmsweep.scenarios.base import run_python
from llmsweep.scenarios.codegen import Codegen
from llmsweep.scenarios.weather import Weather


@pytest.mark.parametrize(
    "answer,called,expected",
    [
        ("18 C, 64.4 °F, 12:00", list(Weather.expected_tools), True),
        ("64.4 F", ["get_weather", "convert_temperature"], False),
        ("65 F", list(Weather.expected_tools), False),
    ],
)
async def test_weather(answer: str, called: list[str], expected: bool) -> None:
    scenario = Weather()
    try:
        assert (await scenario.score(answer, called))[0] is expected
        assert await scenario.call(
            "convert_temperature", {"value": 18, "from_unit": "c", "to_unit": "f"}
        ) == {"value": 64.4, "unit": "f"}
        assert "error" in await scenario.call("get_current_time", {"timezone": "Mars/Nowhere"})
    finally:
        scenario.close()


async def test_agent_code_guards_and_scoring(tmp_path: Path) -> None:
    scenario = AgentCode()
    root = scenario.root
    try:
        for path in ["../escape", "/tmp/escape", "README.md", "tests.py"]:
            assert "error" in await scenario.call("write_file", {"path": path, "content": "x"})
        target = tmp_path / "outside"
        target.write_text("secret")
        (root / "link").symlink_to(target)
        assert "error" in await scenario.call("read_file", {"path": "link"})
        assert not (await scenario.score("fixed", ["write_file"]))[0]
        await scenario.call(
            "write_file",
            {"path": "buggy.py", "content": "def total(items):\n    return sum(items)\n"},
        )
        assert not (await scenario.score("fixed", []))[0]
        assert (await scenario.score("fixed", ["write_file"]))[0]
        (root / "buggy.py").write_text("x\n" * 100)
        assert (await scenario.call("grep", {"pattern": "x", "path": "buggy.py"}))["truncated"]
    finally:
        scenario.close()
    assert not root.exists()


@pytest.mark.parametrize(
    "code,expected",
    [
        ("def fib(n):\n    return n", False),
        ("Here is the fibonacci function you asked for.", False),
        ("import sys\nsys.exit(0)", False),
        (
            "```python\ndef fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a+b\n    return a\n```",
            True,
        ),
    ],
)
async def test_codegen(code: str, expected: bool) -> None:
    scenario = Codegen()
    try:
        assert (await scenario.score(code, []))[0] is expected
    finally:
        scenario.close()


async def test_subprocess_timeout_and_cancel(tmp_path: Path) -> None:
    assert await run_python(tmp_path, "while True: pass", 0.05) == {
        "passed": False,
        "output": "checker timed out",
    }
    task = asyncio.create_task(run_python(tmp_path, "while True: pass", 30))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_custom_weather_unscored() -> None:
    scenario = Weather("custom")
    try:
        assert (await scenario.score("wrong", []))[0] is None
    finally:
        scenario.close()
