from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llmsweep.providers.lmstudio import LMStudio
from llmsweep.runner import RunOptions, RunSession
from llmsweep.store import RunStore, load_run
from tests.fakes.lmstudio import FakeLMStudio, calls, completion, no_wait


def weather_script() -> list[list[bytes]]:
    return [
        calls(
            ("get_weather", {"city": "Paris"}),
            ("convert_temperature", {"value": 18, "from_unit": "c", "to_unit": "f"}),
            ("get_current_time", {"timezone": "Europe/Paris"}),
        ),
        completion("Paris is 18 C / 64.4 F at 12:00."),
    ]


async def test_scored_run_and_repeats(tmp_path: Path) -> None:
    fake = FakeLMStudio()
    fake.scripts = [completion("ok"), *weather_script(), *weather_script()]
    provider = LMStudio(transport=fake.transport, sleep=no_wait)
    try:
        models = [m for m in await provider.list_models() if m.chat]
        session = RunSession(
            provider, RunStore(tmp_path), RunOptions(scenarios=("weather",), repeat=2)
        )
        run = await session.run_all(models)
        result = run.models[0]
        assert result.summary()["success_rate"] == 1
        assert result.load_s == 3.5 and len(result.samples) == 2
        assert sum(r.url.path.endswith("/load") for r in fake.requests) == 1
        assert fake.chat_requests[0]["max_tokens"] == 8
        assert not fake.loaded
        assert load_run(session.store.directory(run)).document() == run.document()
    finally:
        await provider.close()


async def test_cancel_retains_partial_and_unloads(tmp_path: Path) -> None:
    fake = FakeLMStudio()
    fake.gate = asyncio.Event()
    provider = LMStudio(transport=fake.transport, sleep=no_wait)
    session = RunSession(
        provider, RunStore(tmp_path), RunOptions(no_warmup=True, scenarios=("weather",))
    )
    try:
        models = [m for m in await provider.list_models() if m.chat]
        session.prepare(models)
        task = asyncio.create_task(session.run_model(models[0].ref))
        for _ in range(100):
            await asyncio.sleep(0)
            if fake.chat_requests:
                break
        assert fake.loaded
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        session.finish()
        assert not fake.loaded
        assert load_run(session.store.directory(session.run)).status == "cancelled"
    finally:
        await provider.close()
