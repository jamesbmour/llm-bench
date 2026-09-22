"""Strict TOML, environment, and command-line configuration resolution."""

from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from platformdirs import user_config_path

from .errors import ConfigError
from .runner import RunOptions
from .selection import resolve_scenarios

DEFAULTS: dict[str, Any] = {
    "provider": "lmstudio",
    "host": "localhost",
    "port": 1234,
    "base_url": None,
    "api_key_env": "LMSTUDIO_API_KEY",
    "timeout": 300.0,
    "load_timeout": 600.0,
    "scenarios": "weather,agent-code,codegen",
    "repeat": 1,
    "max_tokens": 1024,
    "max_turns": None,
    "parallel": 1,
    "task": None,
    "no_warmup": False,
    "keep_loaded": False,
    "require_tool_use": False,
    "exclude": "",
    "models": None,
    "all": False,
    "sort_by": "order",
    "plain": False,
    "no_color": False,
    "verbose": False,
    "run_store": None,
    "transcript_dir": None,
    "fail_on_regression": False,
    "baseline": None,
}
PROVIDER_KEYS = {"base_url", "host", "port", "api_key_env", "timeout", "load_timeout"}
THRESHOLDS = {"tok_s_regression_pct": 5.0, "ttft_regression_pct": 5.0}
BOOLEAN_KEYS = {key for key, value in DEFAULTS.items() if isinstance(value, bool)}
INTEGER_KEYS = {"port", "repeat", "max_tokens", "max_turns", "parallel"}
FLOAT_KEYS = {"timeout", "load_timeout"}


@dataclass(frozen=True)
class Settings:
    """Resolved configuration with credentials excluded from its representation."""

    values: dict[str, Any]
    api_key: str | None = field(default=None, repr=False)
    thresholds: tuple[float, float] = (5, 5)

    def run_options(self) -> RunOptions:
        v = self.values
        return RunOptions(
            resolve_scenarios(v["scenarios"]),
            v["repeat"],
            v["max_tokens"],
            v["max_turns"],
            v["load_timeout"],
            v["no_warmup"],
            v["keep_loaded"],
            v["parallel"],
            v["task"],
        )


def _read(path: Path) -> tuple[dict[str, Any], dict[str, float]]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from None
    unknown = set(data) - set(DEFAULTS) - {"providers", "thresholds"}
    if unknown:
        raise ConfigError(
            f"unknown config keys: {', '.join(sorted(unknown))}; use api_key_env for secrets"
        )
    providers = data.pop("providers", {})
    thresholds = data.pop("thresholds", {})
    if not isinstance(providers, dict) or not isinstance(thresholds, dict):
        raise ConfigError("providers and thresholds must be TOML tables")
    for block in providers.values():
        if isinstance(block, dict) and "api_key" in block:
            raise ConfigError("literal api_key is forbidden; use api_key_env")
    block = providers.get("lmstudio", {})
    if not isinstance(block, dict) or set(block) - PROVIDER_KEYS:
        raise ConfigError("unknown or invalid providers.lmstudio keys; use api_key_env for secrets")
    data.update(block)
    if set(thresholds) - set(THRESHOLDS):
        raise ConfigError("unknown threshold key")
    for key, value in thresholds.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ConfigError(f"{key} must be a finite non-negative number")
    return data, thresholds


def resolve_config(
    cli: dict[str, Any],
    *,
    environ: dict[str, str] | None = None,
    project_dir: Path | None = None,
    user_dir: Path | None = None,
) -> Settings:
    env = dict(os.environ) if environ is None else environ
    values = dict(DEFAULTS)
    thresholds = dict(THRESHOLDS)
    paths = [(user_dir or user_config_path("llmsweep", appauthor=False)) / "llmsweep.toml"]
    explicit = cli.get("config")
    project_path = Path(explicit) if explicit else (project_dir or Path.cwd()) / "llmsweep.toml"
    if explicit and not project_path.is_file():
        raise ConfigError(f"config does not exist: {project_path}")
    paths.append(project_path)
    for path in paths:
        if path.is_file():
            data, limits = _read(path)
            values.update(data)
            thresholds.update(limits)
    for key in DEFAULTS:
        variable = "LLMSWEEP_" + key.upper()
        if variable not in env:
            continue
        raw = env[variable]
        try:
            if key in BOOLEAN_KEYS:
                if raw.lower() not in ("1", "0", "true", "false", "yes", "no"):
                    raise ValueError
                value: Any = raw.lower() in ("1", "true", "yes")
            elif key in INTEGER_KEYS:
                value = int(raw)
            elif key in FLOAT_KEYS:
                value = float(raw)
            else:
                value = raw
        except ValueError:
            raise ConfigError(f"invalid {variable}") from None
        values[key] = value
    values.update(
        {key: value for key, value in cli.items() if key in DEFAULTS and value is not None}
    )
    if cli.get("providers") not in (None, "lmstudio"):
        raise ConfigError("v1 supports only --providers lmstudio")
    if values["provider"] != "lmstudio":
        raise ConfigError("v1 supports only --provider lmstudio")
    for key in INTEGER_KEYS:
        value = values[key]
        if key == "max_turns" and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"{key} must be a positive integer")
    if values["port"] > 65535:
        raise ConfigError("port must be between 1 and 65535")
    for key in FLOAT_KEYS:
        value = values[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ConfigError(f"{key} must be a finite positive number")
    if values["load_timeout"] > 600:
        raise ConfigError("load_timeout cannot exceed 600 seconds")
    for key in BOOLEAN_KEYS:
        if not isinstance(values[key], bool):
            raise ConfigError(f"{key} must be a boolean")
    for key in set(DEFAULTS) - INTEGER_KEYS - FLOAT_KEYS - BOOLEAN_KEYS:
        if values[key] is not None and not isinstance(values[key], (str, Path)):
            raise ConfigError(f"{key} must be text")
    if cli.get("base_url") and (cli.get("host") is not None or cli.get("port") is not None):
        raise ConfigError("--base-url cannot be combined with --host or --port")
    host = values["host"]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    url = values["base_url"]
    if not url or cli.get("host") is not None or cli.get("port") is not None:
        url = f"http://{host}:{values['port']}"
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ConfigError(
            "base_url must be an HTTP(S) server URL without credentials, query, or fragment"
        )
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    values["base_url"] = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    if values["sort_by"] not in ("order", "tok_s", "ttft", "total", "load", "model"):
        raise ConfigError("unsupported sort; LM Studio has no cost metric")
    if values["models"] and values["all"]:
        raise ConfigError("--models and --all are mutually exclusive")
    if values["task"] is not None:
        if cli.get("scenarios") not in (None, "weather"):
            raise ConfigError("--task is only supported with the weather scenario")
        values["scenarios"] = "weather"
    resolve_scenarios(values["scenarios"])
    key_name = values["api_key_env"]
    api_key = (
        cli.get("api_key")
        or env.get(key_name)
        or env.get("LMSTUDIO_API_KEY")
        or env.get("LLMSWEEP_API_KEY")
    )
    values["no_color"] = values["no_color"] or "NO_COLOR" in env
    return Settings(
        values, api_key, (thresholds["tok_s_regression_pct"], thresholds["ttft_regression_pct"])
    )
