from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from llmsweep import cli
from llmsweep.providers.lmstudio import LMStudio
from llmsweep.store import load_run
from tests.fakes.lmstudio import FakeLMStudio, no_wait
from tests.test_runner import weather_script


def test_plain_cli_scores_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeLMStudio()
    fake.scripts.extend(weather_script())

    def factory(*args: Any, **kwargs: Any) -> LMStudio:
        return LMStudio(transport=fake.transport, sleep=no_wait)

    monkeypatch.setattr(cli, "LMStudio", factory)
    output = tmp_path / "export.json"
    code = cli.main(
        [
            "run",
            "--plain",
            "--all",
            "--scenarios",
            "weather",
            "--no-warmup",
            "--run-store",
            str(tmp_path / "store"),
            "--json",
            str(output),
        ]
    )
    assert code == 0
    assert load_run(output).models[0].summary()["success_rate"] == 1
    assert "lmstudio:fixture" in capsys.readouterr().out


def test_list_alias_and_offline_show_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeLMStudio()
    monkeypatch.setattr(cli, "LMStudio", lambda *a, **kw: LMStudio(transport=fake.transport))
    assert cli.main(["--list", "--providers", "lmstudio"]) == 0
    output = capsys.readouterr().out
    assert "fixture" in output and "embedding" not in output
    from llmsweep.store import RunStore

    store = RunStore(tmp_path)
    run = store.create({})
    store.save(run)
    requests = len(fake.requests)
    assert cli.main(["--show", str(store.directory(run)), "--plain"]) == 0
    csv = tmp_path / "result.csv"
    assert cli.main(["export", str(store.directory(run)), "--csv", str(csv)]) == 0
    assert csv.read_text().startswith("provider,model")
    assert len(fake.requests) == requests
    assert "\x1b" not in capsys.readouterr().out


def test_non_tty_and_invalid_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "1")
    assert not cli.wants_tui()
    assert cli.main(["run", "--provider", "ollama", "--all"]) == 2


def test_theme_flag_only_on_tui_commands() -> None:
    assert cli.parser().parse_args(["run", "--theme", "nord"]).theme == "nord"
    assert cli.parser().parse_args(["show", "run.json", "--theme", "nord"]).theme == "nord"
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["export", "run.json", "--theme", "nord"])
