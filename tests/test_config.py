from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from llmsweep.config import resolve_config
from llmsweep.errors import ConfigError


def test_precedence_and_secrets(tmp_path: Path) -> None:
    user = tmp_path / "user"
    user.mkdir()
    (user / "llmsweep.toml").write_text("repeat=2\n[providers.lmstudio]\nport=1111\n")
    (tmp_path / "llmsweep.toml").write_text(
        'repeat=3\n[providers.lmstudio]\napi_key_env="MY_KEY"\n'
    )
    result = resolve_config(
        {"repeat": 5},
        environ={"LLMSWEEP_REPEAT": "4", "MY_KEY": "secret"},
        project_dir=tmp_path,
        user_dir=user,
    )
    assert result.values["repeat"] == 5
    assert result.values["base_url"] == "http://localhost:1111"
    assert result.api_key == "secret"
    assert "secret" not in repr(result)


@pytest.mark.parametrize(
    "text",
    [
        "unknown=1",
        'api_key="secret"',
        '[providers.lmstudio]\napi_key="secret"',
        "repeat=0",
        "[thresholds]\ntok_s_regression_pct=-1",
    ],
)
def test_invalid_config(tmp_path: Path, text: str) -> None:
    (tmp_path / "llmsweep.toml").write_text(text)
    with pytest.raises(ConfigError):
        resolve_config({}, environ={}, project_dir=tmp_path, user_dir=tmp_path / "user")


def test_task_and_inapplicable_flags(tmp_path: Path) -> None:
    kwargs = {"project_dir": tmp_path, "user_dir": tmp_path}
    assert resolve_config({"task": "custom"}, environ={}, **kwargs).values["scenarios"] == "weather"
    cases: list[dict[str, Any]] = [
        {"provider": "ollama"},
        {"sort_by": "cost"},
        {"task": "custom", "scenarios": "codegen"},
        {"base_url": "http://localhost", "port": 12},
    ]
    for args in cases:
        with pytest.raises(ConfigError):
            resolve_config(args, environ={}, **kwargs)
