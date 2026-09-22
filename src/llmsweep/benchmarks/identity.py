"""Canonical fingerprints for non-secret configuration and task content."""

from __future__ import annotations

import hashlib
import json
from typing import Any

_SECRET_PARTS = ("api_key", "authorization", "secret", "password", "token", "credential")


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def public_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _SECRET_PARTS):
                continue
            cleaned[str(key)] = public_value(item)
        return cleaned
    if isinstance(value, list):
        return [public_value(item) for item in value]
    if isinstance(value, tuple):
        return [public_value(item) for item in value]
    return value


def attempt_id(run_id: str, task_id: str, repeat: int, attempt: int) -> str:
    return fingerprint(
        {"run_id": run_id, "task_id": task_id, "repeat": repeat, "attempt": attempt}
    )[:20]


def config_fingerprint(settings: dict[str, Any], tasks: list[dict[str, Any]]) -> str:
    keys = (
        "scenarios",
        "repeat",
        "max_tokens",
        "max_turns",
        "task",
        "no_warmup",
        "preset",
        "pack",
        "temperature",
        "seed",
        "repeat_mode",
        "min_repeats",
        "max_repeats",
        "precision",
        "time_cap_s",
        "token_cap",
        "context_limit",
    )
    body = {key: settings.get(key) for key in keys}
    body["tasks"] = [
        {
            "suite_id": row["suite_id"],
            "task_id": row["task_id"],
            "content_digest": row["content_digest"],
        }
        for row in tasks
    ]
    return fingerprint(public_value(body))
