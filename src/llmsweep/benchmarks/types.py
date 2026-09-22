"""Immutable suite and task definitions. This module imports neither providers nor runners."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """One versioned task inside a pack or built-in suite."""

    pack_id: str
    pack_version: str
    suite_id: str
    task_id: str
    prompt: str
    evaluator_id: str
    evaluator_version: str
    category: str
    difficulty: str
    partition: str
    requires_tools: bool
    execution: str
    max_turns: int
    max_tokens: int
    task_seconds: float
    content_digest: str
    single_turn: bool = False
    language: str = "python"
    tools: tuple[str, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)

    def identity(self) -> dict[str, str]:
        return {
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "suite_id": self.suite_id,
            "task_id": self.task_id,
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True, slots=True)
class SuiteSpec:
    """Declared capabilities for a suite. Selection uses these, not the suite name."""

    suite_id: str
    version: str
    title: str
    requires_tools: bool
    execution: str
    languages: tuple[str, ...]
    tasks: tuple[TaskSpec, ...]

    @property
    def task_count(self) -> int:
        return len(self.tasks)
