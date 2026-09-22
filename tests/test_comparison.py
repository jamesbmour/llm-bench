from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import Button, DataTable, Input, Static

from llmsweep import cli
from llmsweep.comparison import (
    build_ascii_plot,
    build_views,
    comparison_to_csv,
    comparison_to_json,
    comparison_to_markdown,
    export_comparison,
)
from llmsweep.config import resolve_config
from llmsweep.metrics import TurnMetrics
from llmsweep.models import ModelInfo, ModelRef
from llmsweep.results import ModelResult, RunResult, SampleResult
from llmsweep.store import atomic_write, canonical_json
from llmsweep.tui.app import SweepApp
from llmsweep.tui.screens.main import ComparisonScreen, PathDialog, ResultsScreen


def make_turn(tok_s: float = 50.0, output_tokens: int = 100) -> TurnMetrics:
    return TurnMetrics(
        ttft_ms=150.0,
        generation_s=output_tokens / tok_s,
        output_tokens=output_tokens,
        token_source="usage",
        total_tokens=output_tokens + 20,
    )


def make_run(
    run_id: str,
    target: str,
    *,
    fingerprint: str = "fp-1",
    scenario: str = "agent_code",
    successes: list[bool | None] | None = None,
    total_s: float = 2.5,
    tok_s: float = 40.0,
    diagnostic: bool = False,
) -> RunResult:
    if successes is None:
        successes = [True, True, False, True, False]
    ref = ModelRef("lmstudio", target)
    model_info = ModelInfo(ref=ref, type="chat", params_b=7.0, quantization="Q4", tool_use=True)
    samples = []
    for i, succ in enumerate(successes):
        status = "completed" if succ is not None else "error"
        turns = [make_turn(tok_s=tok_s)] if succ is not None else []
        samples.append(
            SampleResult(
                scenario=scenario,
                repeat=i + 1,
                status=status,
                success=succ,
                total_s=total_s if succ is not None else 0.0,
                turns=turns,
                suite_id=scenario,
                task_id=f"{scenario}_task_{i % 2}",
                diagnostic=diagnostic,
            )
        )
    model_res = ModelResult(model=model_info, status="completed", samples=samples, total_s=10.0)
    return RunResult(
        run_id=run_id,
        started_at="2026-09-22T00:00:00Z",
        settings={"scenarios": [scenario], "benchmark_version": "1.0"},
        models=[model_res],
        status="completed",
        fingerprint=fingerprint,
        diagnostic=diagnostic,
    )


def test_build_views_computes_confidence_sample_counts_and_coverage() -> None:
    run1 = make_run("run-1", "model-a", successes=[True, True, True, False, True], total_s=3.0)
    views = build_views([run1])
    assert len(views["points"]) == 1
    point = views["points"][0]

    assert point["run_id"] == "run-1"
    assert point["target"] == "lmstudio:model-a"
    assert point["samples"] == 5
    assert point["scored"] == 5
    assert point["failures"] == 1
    assert point["success_rate"] == 0.8
    assert point["coverage"] == 1.0
    assert point["incomplete"] is False
    assert point["confidence_interval"] is not None
    ci_low, ci_high = point["confidence_interval"]
    assert 0.0 < ci_low < 0.8 < ci_high < 1.0
    assert point["task_duration_s"] == 3.0
    assert point["timing_comparable"] is True
    assert point["unavailable_metrics"] == []


def test_build_views_handles_incomplete_and_unavailable_metrics() -> None:
    # Run with errors and missing timing metrics
    ref = ModelRef("lmstudio", "broken-model")
    model_info = ModelInfo(ref=ref, type="chat", params_b=7.0, quantization="Q4", tool_use=True)
    samples = [
        SampleResult(
            scenario="agent_code",
            repeat=1,
            status="error",
            success=None,
            total_s=0.0,
            suite_id="agent_code",
            task_id="task_1",
        ),
        SampleResult(
            scenario="agent_code",
            repeat=2,
            status="completed",
            success=False,
            total_s=2.0,
            suite_id="agent_code",
            task_id="task_2",
            turns=[],  # no turns -> tok_s is None
        ),
    ]
    model_res = ModelResult(model=model_info, status="error", samples=samples)
    run = RunResult(
        run_id="run-incomplete",
        started_at="2026-09-22T00:00:00Z",
        settings={},
        models=[model_res],
        status="error",
        fingerprint="fp-1",
    )

    views = build_views([run])
    point = views["points"][0]
    assert point["samples"] == 2
    assert point["scored"] == 1
    assert point["coverage"] == 0.5
    assert point["incomplete"] is True
    assert point["success_rate"] == 0.0
    assert point["tok_s"] is None
    assert "tok_s" in point["unavailable_metrics"]


def test_build_views_filtering() -> None:
    run1 = make_run("run-1", "qwen-coder", scenario="codegen", fingerprint="fp-1")
    run2 = make_run("run-2", "llama-instruct", scenario="agent_code", fingerprint="fp-2")

    # Filter target
    v1 = build_views([run1, run2], target="qwen")
    assert len(v1["points"]) == 1
    assert v1["points"][0]["target"] == "lmstudio:qwen-coder"

    # Filter benchmark
    v2 = build_views([run1, run2], benchmark="agent")
    assert len(v2["points"]) == 1
    assert v2["points"][0]["target"] == "lmstudio:llama-instruct"

    # Filter configuration
    v3 = build_views([run1, run2], configuration="fp-2")
    assert len(v3["points"]) == 1
    assert v3["points"][0]["run_id"] == "run-2"

    # Filter task
    v4 = build_views([run1, run2], task="task_0")
    assert len(v4["points"]) == 2


def test_timing_comparability_disabled_when_configurations_differ() -> None:
    run1 = make_run("run-1", "model-a", fingerprint="config-A")
    run2 = make_run("run-2", "model-a", fingerprint="config-B")
    views = build_views([run1, run2])
    # Each run has a point
    assert len(views["points"]) == 2
    for p in views["points"]:
        assert p["timing_comparable"] is True  # individual point within its run

    # Diagnostic run disables timing comparability
    run_diag = make_run("run-diag", "model-a", fingerprint="config-A", diagnostic=True)
    views_diag = build_views([run_diag])
    assert views_diag["points"][0]["timing_comparable"] is False


def test_build_ascii_plot_and_tables_agree() -> None:
    run1 = make_run("run-1", "model-fast", total_s=1.5, tok_s=60.0)
    run2 = make_run("run-2", "model-slow", total_s=4.5, tok_s=20.0)
    view = build_views([run1, run2])

    plot_dur = build_ascii_plot(view["points"], "task_duration_s")
    assert "Success Rate vs Duration (s)" in plot_dur
    assert "model-fast" in plot_dur
    assert "model-slow" in plot_dur
    assert "[1]" in plot_dur
    assert "[2]" in plot_dur

    assert "No data to plot" in build_ascii_plot([], "task_duration_s")


def test_comparison_exports(tmp_path: Path) -> None:
    run1 = make_run("run-1", "model-a", total_s=2.0)
    run2 = make_run("run-2", "model-b", total_s=3.0)
    view = build_views([run1, run2])

    # JSON export
    json_str = comparison_to_json(view)
    parsed = json.loads(json_str)
    assert "points" in parsed
    assert len(parsed["points"]) == 2

    # CSV export
    csv_str = comparison_to_csv(view)
    assert "run_id,target,target_kind,benchmark" in csv_str
    assert "model-a" in csv_str
    assert "model-b" in csv_str

    # Markdown export
    md_str = comparison_to_markdown(view)
    assert "# Quality-Speed Comparison Report" in md_str
    assert "## Success vs Task Duration" in md_str
    assert "## Success vs Throughput (tok/s)" in md_str
    assert "model-a" in md_str
    assert "```text" in md_str

    # File export
    json_file = tmp_path / "comp.json"
    csv_file = tmp_path / "comp.csv"
    md_file = tmp_path / "comp.md"
    export_comparison(
        view,
        json_path=json_file,
        csv_path=csv_file,
        markdown_path=md_file,
    )
    assert json_file.is_file()
    assert csv_file.is_file()
    assert md_file.is_file()


def test_cli_compare_plain_and_exports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run1 = make_run("run-1", "model-a", fingerprint="fp-1")
    run2 = make_run("run-2", "model-a", fingerprint="fp-1")
    p1 = tmp_path / "r1.json"
    p2 = tmp_path / "r2.json"
    atomic_write(p1, canonical_json(run1.document()))
    atomic_write(p2, canonical_json(run2.document()))

    json_out = tmp_path / "out.json"
    csv_out = tmp_path / "out.csv"
    md_out = tmp_path / "out.md"

    code = cli.main(
        [
            "compare",
            str(p1),
            str(p2),
            "--plain",
            "--json",
            str(json_out),
            "--csv",
            str(csv_out),
            "--markdown",
            str(md_out),
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "points\t2" in captured.out
    assert json_out.is_file()
    assert csv_out.is_file()
    assert md_out.is_file()


@pytest.mark.asyncio
async def test_comparison_screen_tui_navigation_and_toggle(tmp_path: Path) -> None:
    run1 = make_run("run-1", "model-a", total_s=2.0, tok_s=40.0)
    run2 = make_run("run-2", "model-b", total_s=4.0, tok_s=20.0)
    settings_obj = resolve_config({"theme": "textual-dark", "no_color": True})

    app = SweepApp(settings_obj, comparison_runs=[run1, run2])
    async with app.run_test() as pilot:
        assert isinstance(app.screen, ComparisonScreen)
        screen = app.screen

        # Verify initial table and description
        desc = str(screen.query_one("#compare-description", Static).render())
        assert "2 run(s)" in desc
        assert "Task Duration" in desc

        table = screen.query_one("#compare-table", DataTable)
        assert table.row_count == 2

        # Verify detail panel populated
        detail = str(screen.query_one("#compare-detail", Static).render())
        assert "model-a" in detail or "model-b" in detail

        # Toggle view to Throughput with key 'v'
        await pilot.press("v")
        desc_after = str(screen.query_one("#compare-description", Static).render())
        assert "Throughput" in desc_after
        toggle_btn = screen.query_one("#compare-toggle-button", Button)
        assert "Throughput" in str(toggle_btn.label)

        # Filter by target
        target_input = screen.query_one("#compare-filter-target", Input)
        target_input.value = "model-a"
        await pilot.pause()
        assert table.row_count == 1

        # Clear filter
        target_input.value = ""
        await pilot.pause()
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_open_comparison_from_results_screen(tmp_path: Path) -> None:
    baseline_run = make_run("base-1", "model-a", total_s=2.0)
    base_path = tmp_path / "baseline.json"
    atomic_write(base_path, canonical_json(baseline_run.document()))

    current_run = make_run("curr-1", "model-a", total_s=2.5)
    settings_obj = resolve_config(
        {"theme": "textual-dark", "no_color": True, "baseline": str(base_path)}
    )

    app = SweepApp(settings_obj, saved_run=current_run)
    async with app.run_test() as pilot:
        assert isinstance(app.screen, ResultsScreen)
        # Press 'c' to open comparison
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, ComparisonScreen)
        assert len(app.screen.runs) == 2

        # Test export shortcut 'e' opens PathDialog
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, PathDialog)
        # Cancel dialog with escape
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ComparisonScreen)

        # Back to ResultsScreen with escape
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ResultsScreen)
