"""Named run profiles. Credentials are never stored."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from llmsweep.benchmarks.identity import public_value
from llmsweep.errors import ConfigError

PROFILE_KEYS = (
    "provider",
    "host",
    "port",
    "base_url",
    "timeout",
    "load_timeout",
    "scenarios",
    "repeat",
    "max_tokens",
    "max_turns",
    "parallel",
    "task",
    "no_warmup",
    "keep_loaded",
    "require_tool_use",
    "exclude",
    "models",
    "sort_by",
    "preset",
    "pack",
    "repeat_mode",
    "min_repeats",
    "max_repeats",
    "precision",
    "time_cap_s",
    "token_cap",
    "temperature",
    "seed",
    "context_limit",
    "tasks",
)


def profile_document(values: dict[str, Any]) -> dict[str, Any]:
    cleaned = public_value(
        {key: values[key] for key in PROFILE_KEYS if key in values and values[key] is not None}
    )
    if not isinstance(cleaned, dict):
        raise ConfigError("profile values must be a table")
    return cleaned


def load_profile_file(path: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read profile {path}: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError("profile must be a TOML table")
    unknown = set(data) - set(PROFILE_KEYS)
    if unknown:
        raise ConfigError(f"unknown profile keys: {', '.join(sorted(unknown))}")
    if "api_key" in data:
        raise ConfigError("profiles cannot contain api_key")
    return data


def resolve_profile_path(name: str, project_dir: Path, user_dir: Path) -> Path:
    for directory in (project_dir / "profiles", user_dir / "profiles"):
        path = directory / f"{name}.toml"
        if path.is_file():
            return path
    raise ConfigError(f"profile not found: {name}")


def list_profiles(project_dir: Path, user_dir: Path) -> list[str]:
    names: list[str] = []
    for directory in (user_dir / "profiles", project_dir / "profiles"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.toml")):
            if path.stem not in names:
                names.append(path.stem)
    return names


def save_profile(path: Path, values: dict[str, Any]) -> None:
    body = profile_document(values)
    lines = []
    for key in PROFILE_KEYS:
        if key not in body:
            continue
        value = body[key]
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, str):
            rendered = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        else:
            rendered = str(value)
        lines.append(f"{key} = {rendered}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
