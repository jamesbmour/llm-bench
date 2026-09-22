"""Regenerate the README image from labeled fixtures without network access."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from llmsweep.config import resolve_config
from llmsweep.metrics import TurnMetrics
from llmsweep.models import ModelInfo, ModelRef
from llmsweep.results import ModelResult, RunResult, SampleResult
from llmsweep.tui.app import SweepApp

SCREENSHOT_PATH = Path(__file__).resolve().parents[1] / "docs/assets/tui.svg"


def demonstration_run() -> RunResult:
    models = []
    for name, speed, ttft in [
        ("demo-coder-8b", 64.2, 182.0),
        ("demo-general-3b", 108.4, 96.0),
        ("demo-reasoner-14b", 32.8, 410.0),
    ]:
        samples = [
            SampleResult(
                scenario,
                repeat,
                status="completed",
                success=name != "demo-general-3b" or scenario != "agent-code",
                turns=[TurnMetrics(ttft, 100 / (speed + repeat - 1), 100, "usage", 160)],
                total_s=4.0,
            )
            for scenario in ("weather", "agent-code", "codegen")
            for repeat in (1, 2, 3)
        ]
        models.append(
            ModelResult(
                ModelInfo(ModelRef("lmstudio", name)),
                status="completed",
                load_s=2.8,
                samples=samples,
                total_s=40,
            )
        )
    return RunResult("demonstration-fixture", "2026-09-22T00:00:00Z", {}, models, "completed")


async def main() -> None:
    with TemporaryDirectory() as directory:
        settings = resolve_config(
            {}, environ={}, project_dir=Path(directory), user_dir=Path(directory)
        )
        app = SweepApp(settings, saved_run=demonstration_run())
        async with app.run_test(size=(120, 32)) as pilot:
            await pilot.pause()
            destination = SCREENSHOT_PATH
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(app.export_screenshot(title="llmsweep · demonstration fixtures"))
            print(destination)


if __name__ == "__main__":
    asyncio.run(main())
