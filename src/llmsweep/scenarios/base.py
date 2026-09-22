from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any, ClassVar


class Scenario:
    name = ""
    prompt = ""
    max_turns = 1
    expected_tools: tuple[str, ...] = ()
    tools: ClassVar[list[dict[str, Any]]] = []

    def __init__(self) -> None:
        self.workspace = tempfile.TemporaryDirectory(prefix="llmsweep-")
        self.root = Path(self.workspace.name).resolve()

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"error": f"unknown tool: {name}"}

    async def score(self, answer: str, called: list[str]) -> tuple[bool | None, str]:
        raise NotImplementedError

    def close(self) -> None:
        self.workspace.cleanup()

    def path(self, name: str) -> Path:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("absolute paths and traversal are not allowed")
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("path escapes the workspace")
        return resolved


def tool(
    name: str, description: str, properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required if required is not None else list(properties),
                "additionalProperties": False,
            },
        },
    }


async def run_python(root: Path, script: str, deadline: float = 15) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        "-c",
        script,
        cwd=root,
        env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    tail = bytearray()

    async def drain() -> int:
        assert process.stdout is not None
        while chunk := await process.stdout.read(4096):
            tail.extend(chunk)
            del tail[:-2000]
        return await process.wait()

    async def stop() -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()

    try:
        async with asyncio.timeout(deadline):
            code = await drain()
        return {"passed": code == 0, "output": tail.decode("utf-8", "replace")[-500:]}
    except TimeoutError:
        await stop()
        return {"passed": False, "output": "checker timed out"}
    except asyncio.CancelledError:
        await asyncio.shield(stop())
        raise
    finally:
        # Descendants can outlive a successful parent and keep writing to the workspace.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
