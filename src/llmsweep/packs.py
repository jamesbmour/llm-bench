"""Local benchmark packs. Manifests cannot import code or run shell hooks."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llmsweep.benchmarks.identity import fingerprint
from llmsweep.benchmarks.types import TaskSpec
from llmsweep.errors import ConfigError

APPROVED_EVALUATORS = frozenset({"exact", "contains", "regex"})
MAX_FILE_BYTES = 256_000
MAX_FILES = 32
_FORBIDDEN = frozenset({"shell", "command", "import", "hook", "install", "exec", "python"})


@dataclass(frozen=True, slots=True)
class Pack:
    path: Path
    pack_id: str
    version: str
    description: str
    tasks: tuple[TaskSpec, ...]
    digest: str
    needs_isolation: bool


def _reject_forbidden(value: Any, location: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN:
                raise ConfigError(f"{location} contains forbidden key {key}")
            _reject_forbidden(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden(item, f"{location}[{index}]")


def _check_fixture(root: Path, row: dict[str, Any]) -> str:
    raw = row.get("path")
    expected = row.get("sha256")
    if not isinstance(raw, str) or not isinstance(expected, str):
        raise ConfigError("fixture path and sha256 are required")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise ConfigError(f"fixture path escapes the pack: {raw}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ConfigError(f"fixture path escapes the pack: {raw}")
    if path.is_symlink():
        raise ConfigError(f"fixture symlink is not allowed: {raw}")
    if not path.is_file():
        raise ConfigError(f"fixture is missing: {raw}")
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise ConfigError(f"fixture exceeds {MAX_FILE_BYTES} bytes: {raw}")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ConfigError(f"fixture hash mismatch for {raw}")
    return actual


def load_pack(path: Path) -> Pack:
    root = path if path.is_dir() else path.parent
    manifest = path / "pack.toml" if path.is_dir() else path
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read pack {manifest}: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError("pack manifest must be a table")
    _reject_forbidden(data, "pack")
    if data.get("schema") != 1:
        raise ConfigError("pack schema must be 1")
    pack_id = data.get("id")
    version = data.get("version")
    if not isinstance(pack_id, str) or not pack_id or not isinstance(version, str) or not version:
        raise ConfigError("pack id and version are required")
    rows = data.get("tasks")
    if not isinstance(rows, list) or not rows:
        raise ConfigError("pack tasks must be a non-empty array")
    if len(rows) > MAX_FILES:
        raise ConfigError("pack has too many tasks")
    seen: set[str] = set()
    tasks: list[TaskSpec] = []
    fixture_hashes: list[str] = []
    needs_isolation = False
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ConfigError(f"task {index} must be a table")
        task_id = row.get("id")
        prompt = row.get("prompt")
        evaluator = row.get("evaluator")
        if not isinstance(task_id, str) or not task_id:
            raise ConfigError("task id is required")
        if task_id in seen:
            raise ConfigError(f"duplicate task id {task_id}")
        seen.add(task_id)
        if not isinstance(prompt, str) or not prompt:
            raise ConfigError(f"task {task_id} needs a prompt")
        if evaluator not in APPROVED_EVALUATORS:
            raise ConfigError(f"task {task_id} evaluator {evaluator!r} is not approved")
        tools = row.get("tools", [])
        if not isinstance(tools, list) or any(not isinstance(item, str) for item in tools):
            raise ConfigError(f"task {task_id} tools must be a list of names")
        if tools:
            needs_isolation = True
        for name in ("max_turns", "max_tokens"):
            value = row.get(name, 1 if name == "max_turns" else 256)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ConfigError(f"task {task_id} {name} must be a positive integer")
        seconds = row.get("task_seconds", 60)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
            raise ConfigError(f"task {task_id} task_seconds must be positive")
        fixtures = row.get("fixtures", [])
        if not isinstance(fixtures, list):
            raise ConfigError(f"task {task_id} fixtures must be a list")
        hashes = [_check_fixture(root, fixture) for fixture in fixtures]
        fixture_hashes.extend(hashes)
        expected = row.get("expected", "")
        if not isinstance(expected, str):
            raise ConfigError(f"task {task_id} expected must be text")
        payload = {
            "evaluator": evaluator,
            "expected": expected,
            "fixtures": hashes,
            "tools": list(tools),
        }
        tasks.append(
            TaskSpec(
                pack_id=pack_id,
                pack_version=version,
                suite_id=str(row.get("suite") or pack_id),
                task_id=task_id,
                prompt=prompt,
                evaluator_id=str(evaluator),
                evaluator_version="1",
                category=str(row.get("category") or "custom"),
                difficulty=str(row.get("difficulty") or "medium"),
                partition=str(row.get("partition") or "eval"),
                requires_tools=bool(tools),
                execution="isolated" if tools else "workflow",
                max_turns=int(row.get("max_turns", 1)),
                max_tokens=int(row.get("max_tokens", 256)),
                task_seconds=float(seconds),
                content_digest=fingerprint(payload | {"prompt": prompt, "id": task_id}),
                single_turn=int(row.get("max_turns", 1)) == 1,
                tools=tuple(tools),
                payload=payload,
            )
        )
    digest = fingerprint(
        {
            "id": pack_id,
            "version": version,
            "tasks": [task.identity() for task in tasks],
            "fixtures": fixture_hashes,
        }
    )
    return Pack(
        root,
        pack_id,
        version,
        str(data.get("description") or ""),
        tuple(tasks),
        digest,
        needs_isolation,
    )


def grade_pack_task(answer: str, payload: dict[str, Any]) -> tuple[bool, str, str | None]:
    expected = str(payload.get("expected", ""))
    kind = str(payload.get("evaluator"))
    text = answer.strip()
    if kind == "exact":
        passed = text == expected or text.casefold() == expected.casefold()
    elif kind == "contains":
        passed = expected.casefold() in text.casefold()
    elif kind == "regex":
        import re

        try:
            passed = re.search(expected, text) is not None
        except re.error as exc:
            raise ConfigError(f"invalid pack regex: {exc}") from None
    else:
        raise ConfigError(f"unsupported evaluator {kind}")
    if passed:
        return True, "matched", None
    return False, "not matched", "incorrect"
