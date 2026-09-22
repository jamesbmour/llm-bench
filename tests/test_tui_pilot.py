from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import DataTable, Input
from textual.worker import WorkerState

from llmsweep.config import Settings, resolve_config
from llmsweep.models import ModelInfo, ModelRef
from llmsweep.providers.lmstudio import LMStudio, sse
from llmsweep.results import ModelResult, RunResult, SampleResult
from llmsweep.runner import RunOptions, RunSession
from llmsweep.store import RunStore, load_run
from llmsweep.tui.app import SweepApp
from llmsweep.tui.screens.main import LiveScreen, PickerScreen, ResultsScreen, TranscriptScreen
from tests.fakes.lmstudio import FakeLMStudio, completion, no_wait


def settings(tmp_path: Path, *, all_models: bool = True) -> Settings:
    return resolve_config(
        {"run_store": str(tmp_path), "all": all_models, "no_warmup": True, "scenarios": "codegen"},
        environ={},
        project_dir=tmp_path,
        user_dir=tmp_path,
    )


async def until(pilot: Pilot[int], predicate: Callable[[], bool]) -> None:
    for _ in range(150):
        if predicate():
            return
        await pilot.pause(0.01)
    assert predicate(), "UI did not reach expected state"


async def test_cancel_mid_stream_keeps_app_and_cleans_instances(tmp_path: Path) -> None:
    fake = FakeLMStudio()
    fake.gate = asyncio.Event()
    provider = LMStudio(transport=fake.transport, sleep=no_wait)
    app = SweepApp(settings(tmp_path), provider=provider)
    async with app.run_test(size=(100, 40)) as pilot:
        await until(pilot, lambda: bool(fake.chat_requests))
        assert isinstance(app.screen, LiveScreen) and fake.loaded
        await pilot.press("ctrl+c")
        assert app.is_running
        await pilot.press("ctrl+x")
        await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
        assert not fake.loaded
        assert app.session is not None
        run = load_run(app.session.store.directory(app.session.run))
        assert run.status == "cancelled"
        assert app.is_running


async def test_worker_error_does_not_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLMStudio()
    provider = LMStudio(transport=fake.transport, sleep=no_wait)
    session = RunSession(provider, RunStore(tmp_path), RunOptions(no_warmup=True))

    async def raising(ref: ModelRef) -> ModelResult:
        raise RuntimeError("scripted worker failure")

    monkeypatch.setattr(session, "run_model", raising)
    app = SweepApp(settings(tmp_path), session=session)
    async with app.run_test(size=(100, 35)) as pilot:
        await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
        assert app.is_running and app.error == "scripted worker failure"
        worker = next(iter(app.model_workers.values()))
        assert worker.state == WorkerState.ERROR and isinstance(worker.error, RuntimeError)
        assert session.run.models[0].error == "scripted worker failure"


async def test_picker_uses_shared_selection_and_keys(tmp_path: Path) -> None:
    fake = FakeLMStudio()
    app = SweepApp(
        settings(tmp_path, all_models=False),
        provider=LMStudio(transport=fake.transport, sleep=no_wait),
    )
    async with app.run_test(size=(100, 35)) as pilot:
        await until(pilot, lambda: app.discovered)
        assert isinstance(app.screen, PickerScreen)
        picker = app.screen
        picker.query_one("#model-selection", Input).value = "1"
        await pilot.pause()
        assert picker.selected == {ModelRef("lmstudio", "fixture")}
        assert picker.query_one(DataTable).row_count == 1
        await pilot.press("f1")
        assert app.screen.query("HelpPanel")


async def test_resize_and_coalescing_preserve_output(tmp_path: Path) -> None:
    fake = FakeLMStudio()
    fake.gate = asyncio.Event()
    fake.pause_after = 750
    fake.scripts = [[sse({"choices": [{"delta": {"content": "x"}}]}) for _ in range(1500)]]
    provider = LMStudio(transport=fake.transport, sleep=no_wait)
    app = SweepApp(settings(tmp_path), provider=provider)
    async with app.run_test(size=(130, 45)) as pilot:
        await until(
            pilot,
            lambda: (
                app.live is not None
                and bool(app.live.cards)
                and next(iter(app.live.cards.values())).output_text.count("x") == 750
            ),
        )
        assert app.live is not None
        card = next(iter(app.live.cards.values()))
        await pilot.resize_terminal(75, 28)
        await pilot.pause()
        assert card.output_text.count("x") == 750
        fake.gate.set()
        await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
        assert card.output_text.count("x") == 1500
        assert card.updates < 30
        assert app.session is not None
        assert app.session.run.models[0].samples[0].answer == "x" * 1500


async def test_offline_transcript_and_export_dialog(tmp_path: Path) -> None:
    model = ModelResult(
        ModelInfo(ModelRef("lmstudio", "offline")),
        status="completed",
        samples=[
            SampleResult(
                "weather",
                1,
                status="completed",
                messages=[{"role": "tool", "content": '{"value":64.4}'}],
            )
        ],
    )
    run = RunResult("offline", "now", {}, [model], status="completed")
    app = SweepApp(settings(tmp_path), saved_run=run)
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.press("enter")
        await until(pilot, lambda: isinstance(app.screen, TranscriptScreen))
        assert isinstance(app.screen, TranscriptScreen)
        assert '"value": 64.4' in app.screen.text
        await pilot.press("escape")
        await pilot.press("e")
        await pilot.pause()
        destination = tmp_path / "export.json"
        app.screen.query_one(Input).value = str(destination)
        await pilot.press("enter")
        await until(pilot, destination.exists)
        assert load_run(destination).run_id == "offline"
        assert app.provider is None


async def test_plain_and_tui_store_bytes_identical(tmp_path: Path) -> None:
    def build(root: Path) -> tuple[RunSession, FakeLMStudio]:
        fake = FakeLMStudio()
        fake.scripts = [
            completion("def fib(n):\n a,b=0,1\n for _ in range(n): a,b=b,a+b\n return a")
        ]
        counter = iter(range(10000))
        provider = LMStudio(transport=fake.transport, sleep=no_wait)
        options = RunOptions(no_warmup=True, scenarios=("codegen",))
        store = RunStore(root)
        run = store.create(options.settings(), run_id="same", started_at="same")
        session = RunSession(provider, store, options, clock=lambda: next(counter) / 10, run=run)
        return session, fake

    plain, _ = build(tmp_path / "plain")
    try:
        models = [m for m in await plain.provider.list_models() if m.chat]
        await plain.run_all(models)
    finally:
        await plain.provider.close()
    tui, _ = build(tmp_path / "tui")
    app = SweepApp(settings(tmp_path / "tui"), session=tui)
    async with app.run_test(size=(100, 35)) as pilot:
        await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
    assert (plain.store.directory(plain.run) / "run.json").read_bytes() == (
        tui.store.directory(tui.run) / "run.json"
    ).read_bytes()


async def test_rerunning_one_model_preserves_sibling_worker(tmp_path: Path) -> None:
    fake = FakeLMStudio(preloaded=True)
    fake.extra_models = ["second"]
    fake.loaded["second"] = "second-preexisting"
    fake.gate = asyncio.Event()
    config = resolve_config(
        {
            "run_store": str(tmp_path),
            "all": True,
            "parallel": 2,
            "no_warmup": True,
            "scenarios": "codegen",
        },
        environ={},
        project_dir=tmp_path,
        user_dir=tmp_path,
    )
    app = SweepApp(config, provider=LMStudio(transport=fake.transport, sleep=no_wait))
    first, second = ModelRef("lmstudio", "fixture"), ModelRef("lmstudio", "second")
    async with app.run_test(size=(130, 45)) as pilot:
        await until(pilot, lambda: len(fake.chat_requests) == 2)
        original = app.model_workers[first]
        sibling = app.model_workers[second]
        app.rerun_model(first)
        await until(pilot, lambda: len(fake.chat_requests) == 3)
        assert original.is_cancelled
        assert app.model_workers[second] is sibling and not sibling.is_cancelled
        assert app.is_running
        await pilot.press("ctrl+x")
        await until(pilot, lambda: isinstance(app.screen, ResultsScreen))
        assert fake.loaded == {"fixture": "preexisting", "second": "second-preexisting"}
