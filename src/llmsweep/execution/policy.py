"""Execution-policy detection. New executable work does not silently downgrade."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass

from llmsweep.errors import ConfigError


@dataclass(frozen=True, slots=True)
class PolicyStatus:
    name: str
    available: bool
    detail: str


def workflow_policy() -> PolicyStatus:
    return PolicyStatus("workflow", True, "temporary workspace and process group")


def isolated_policy(which: Callable[[str], str | None] | None = None) -> PolicyStatus:
    finder = which or shutil.which
    if finder("sandbox-exec"):
        return PolicyStatus("isolated", True, "sandbox-exec")
    if finder("bwrap"):
        return PolicyStatus("isolated", True, "bwrap")
    return PolicyStatus("isolated", False, "sandbox-exec and bwrap are unavailable")


def require_isolation(status: PolicyStatus, reason: str) -> None:
    if not status.available:
        raise ConfigError(f"isolated execution is required for {reason} but {status.detail}")
