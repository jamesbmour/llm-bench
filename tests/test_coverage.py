from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import Button, DataTable, Input, Static

from llmsweep import cli
from llmsweep.config import resolve_config
from llmsweep.coverage import (
    build_coverage_matrix,
    coverage_to_csv,
    coverage_to_json,
    coverage_to_markdown,
    export_coverage,
)
from llmsweep.metrics import TurnMetrics
from llmsweep.models import ModelInfo, ModelRef
from llmsweep.results import ModelResult, RunResult, SampleResult
from llmsweep.store import atomic_write, canonical_json
from llmsweep.tui.app import SweepApp
from llmsweep.tui.screens.main import CoverageScreen, PathDialog, ResultsScreen


def make_turn(tok_s: float = 50.0, output_tokens: int = 100) -> TurnMetrics:
    return TurnMetrics(
        ttft_ms=150.0,
        generation_s=output_tokens / tok_s,
        output_tokens=output_tokens,
        token_source="usage",
        total_tokens=output_tokens + 20,
    )


def make_sample(
    scenario: str,
    repeat: int,
    status: str,
    success: bool | None,
    task_id: str = "task_0",
    total_s: float = 2.0,
) -> SampleResult:
    turns = [make_turn()] if status == "completed" else []
    return SampleResult(
        scenario=scenario,
        repeat=repeat,
        status=status,
        success=success,
        total_s=total_s if status == "completed" else 0.0,
        turns=turns,
        suite_id=scenario,
        task_id=task_id,
    )


def make_test_run(
    run_id: str = "run-cov",
    fingerprint: str = "fp-cov",
    planned_scenarios: list[str] | None = None,
    repeats: int = 5,
) -> RunResult:
    if planned_scenarios is None:
        planned_scenarios = ["agent-code", "code-edge", "weather", "unrun-bench"]

    ref1 = ModelRef("lmstudio", "model-strong")
    ref2 = ModelRef("lmstudio", "model-mixed")
    info1 = ModelInfo(ref=ref1, type="chat", params_b=7.0, quantization="Q4", tool_use=True)
    info2 = ModelInfo(ref=ref2, type="chat", params_b=13.0, quantization="Q4", tool_use=True)

    # model-strong: complete on agent-code and weather
    samples1 = [
        make_sample("agent-code", 1, "completed", True, "ac_1"),
        make_sample("agent-code", 2, "completed", True, "ac_1"),
        make_sample("agent-code", 3, "completed", True, "ac_2"),
        make_sample("agent-code", 4, "completed", True, "ac_2"),
        make_sample("agent-code", 5, "completed", False, "ac_2"),
        make_sample("weather", 1, "completed", True, "w_1"),
        make_sample("weather", 2, "completed", True, "w_1"),
        make_sample("weather", 3, "completed", True, "w_1"),
        make_sample("weather", 4, "completed", True, "w_1"),
        make_sample("weather", 5, "completed", True, "w_1"),
    ]

    # model-mixed: incomplete on agent-code, all errors on code-edge (unavailable)
    samples2 = [
        make_sample("agent-code", 1, "completed", True, "ac_1"),
        make_sample("agent-code", 2, "completed", False, "ac_1"),
        make_sample("agent-code", 3, "skipped", None, "ac_2"),
        make_sample("agent-code", 4, "cancelled", None, "ac_2"),
        make_sample("code-edge", 1, "error", None, "ce_1"),
        make_sample("code-edge", 2, "error", None, "ce_2"),
    ]

    m1 = ModelResult(model=info1, status="completed", samples=samples1)
    m2 = ModelResult(model=info2, status="error", samples=samples2)

    return RunResult(
        run_id=run_id,
        started_at="2026-09-22T00:00:00Z",
        settings={"scenarios": planned_scenarios, "repeats": repeats},
        models=[m1, m2],
        status="completed",
        fingerprint=fingerprint,
    )


def test_build_coverage_matrix_complete_incomplete_unrun_unavailable() -> None:
    run = make_test_run()
    matrix = build_coverage_matrix([run])

    cells = matrix["cells"]

    # 1. Complete cell: model-strong on weather (5/5 completed, 100% pass)
    c_weather_strong = cells["lmstudio:model-strong::weather"]
    assert c_weather_strong["status"] == "complete"
    assert c_weather_strong["success_rate"] == 1.0
    assert c_weather_strong["completed"] == 5
    assert c_weather_strong["planned"] == 5
    assert c_weather_strong["coverage"] == 1.0
    assert "100% (5/5)" in c_weather_strong["label"]

    # 2. Incomplete cell: model-mixed on agent-code (2 completed / 5 planned)
    c_agent_mixed = cells["lmstudio:model-mixed::agent-code"]
    assert c_agent_mixed["status"] == "incomplete"
    assert c_agent_mixed["success_rate"] == 0.5
    assert c_agent_mixed["completed"] == 2
    assert c_agent_mixed["planned"] == 5
    assert c_agent_mixed["coverage"] == 0.4
    assert "inc" in c_agent_mixed["label"]

    # 3. Unavailable cell: model-mixed on code-edge (all errors, 0 scored)
    c_edge_mixed = cells["lmstudio:model-mixed::code-edge"]
    assert c_edge_mixed["status"] == "unavailable"
    assert c_edge_mixed["success_rate"] is None  # Never appears as zero!
    assert c_edge_mixed["completed"] == 0
    assert "unavail" in c_edge_mixed["label"]

    # 4. Unrun cell: model-strong on unrun-bench (planned in scenarios, 0 samples)
    c_unrun_strong = cells["lmstudio:model-strong::unrun-bench"]
    assert c_unrun_strong["status"] == "unrun"
    assert c_unrun_strong["success_rate"] is None  # Never appears as zero!
    assert c_unrun_strong["completed"] == 0
    assert c_unrun_strong["coverage"] == 0.0
    assert "unrun (0/5)" in c_unrun_strong["label"]

    # Denominators and summary
    summary = matrix["summary"]
    assert summary["total_models"] == 2
    assert summary["complete_cells"] >= 2
    assert summary["incomplete_cells"] >= 1
    assert summary["unrun_cells"] >= 2
    assert summary["unavailable_cells"] >= 1


def test_coverage_filtering() -> None:
    run1 = make_test_run("run-alpha", "fp-A")
    run2 = make_test_run("run-beta", "fp-B")

    # Filter model
    m_filtered = build_coverage_matrix([run1, run2], model="strong")
    assert m_filtered["models"] == ["lmstudio:model-strong"]

    # Filter category
    c_filtered = build_coverage_matrix([run1, run2], category="tools")
    assert "weather" in c_filtered["benchmarks"]
    assert "agent-code" not in c_filtered["benchmarks"]

    # Filter run_id
    r_filtered = build_coverage_matrix([run1, run2], run_id="beta")
    assert len(r_filtered["models"]) == 2

    # Filter configuration
    cfg_filtered = build_coverage_matrix([run1, run2], configuration="fp-A")
    assert len(cfg_filtered["models"]) == 2


def test_coverage_empty_history() -> None:
    empty_run = RunResult(
        run_id="empty",
        started_at="2026-09-22T00:00:00Z",
        settings={},
        models=[],
        status="completed",
    )
    matrix = build_coverage_matrix([empty_run])
    assert matrix["models"] == []
    assert matrix["benchmarks"] == []
    assert matrix["summary"]["total_cells"] == 0
    assert matrix["summary"]["overall_coverage"] == 0.0

    # Also test empty list
    matrix_empty = build_coverage_matrix([])
    assert matrix_empty["models"] == []


def test_coverage_exports(tmp_path: Path) -> None:
    run = make_test_run()
    matrix = build_coverage_matrix([run])

    # JSON export
    j_text = coverage_to_json(matrix)
    data = json.loads(j_text)
    assert "summary" in data
    assert "cells" in data

    # CSV export
    c_text = coverage_to_csv(matrix)
    assert "model,benchmark,category,status,label" in c_text
    assert "lmstudio:model-strong" in c_text
    assert "weather" in c_text

    # Markdown export
    m_text = coverage_to_markdown(matrix)
    assert "# Benchmark Coverage and Results Dashboard" in m_text
    assert "## Results Matrix" in m_text
    assert "## Detailed Coverage Table" in m_text
    assert "| Model |" in m_text

    # File export
    j_file = tmp_path / "cov.json"
    c_file = tmp_path / "cov.csv"
    m_file = tmp_path / "cov.md"

    export_coverage(
        matrix,
        json_path=j_file,
        csv_path=c_file,
        markdown_path=m_file,
    )
    assert j_file.is_file()
    assert c_file.is_file()
    assert m_file.is_file()


def test_cli_coverage_plain_and_exports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = make_test_run()
    run_file = tmp_path / "run.json"
    atomic_write(run_file, canonical_json(run.document()))

    j_out = tmp_path / "out.json"
    c_out = tmp_path / "out.csv"
    m_out = tmp_path / "out.md"

    code = cli.main(
        [
            "coverage",
            str(run_file),
            "--plain",
            "--json",
            str(j_out),
            "--csv",
            str(c_out),
            "--markdown",
            str(m_out),
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "# Benchmark Coverage and Results Dashboard" in captured.out
    assert j_out.is_file()
    assert c_out.is_file()
    assert m_out.is_file()


@pytest.mark.asyncio
async def test_coverage_screen_tui_matrix_and_table_toggle(tmp_path: Path) -> None:
    run = make_test_run()
    settings_obj = resolve_config({"theme": "textual-dark", "no_color": True})

    app = SweepApp(settings_obj, coverage_runs=[run])
    async with app.run_test() as pilot:
        assert isinstance(app.screen, CoverageScreen)
        screen = app.screen

        # Verify initial description and matrix table
        desc = str(screen.query_one("#coverage-description", Static).render())
        assert "Coverage:" in desc
        assert "model(s)" in desc

        table = screen.query_one("#coverage-table", DataTable)
        assert table.row_count == 2  # 2 models

        # Verify detail panel populated for selected model
        detail = str(screen.query_one("#coverage-detail", Static).render())
        assert "Model: lmstudio:model-" in detail

        # Toggle view to Plain Table with key 't'
        await pilot.press("t")
        toggle_btn = screen.query_one("#coverage-toggle-button", Button)
        assert "Table" in str(toggle_btn.label)
        assert table.row_count > 2  # Plain table lists individual (model, benchmark) pairs

        # Toggle back to Matrix view
        await pilot.press("t")
        assert "Matrix" in str(toggle_btn.label)
        assert table.row_count == 2

        # Filter by model
        m_input = screen.query_one("#coverage-filter-model", Input)
        m_input.value = "strong"
        await pilot.pause()
        assert table.row_count == 1

        # Clear filter
        m_input.value = ""
        await pilot.pause()
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_open_coverage_from_results_screen(tmp_path: Path) -> None:
    run = make_test_run()
    settings_obj = resolve_config({"theme": "textual-dark", "no_color": True})

    app = SweepApp(settings_obj, saved_run=run)
    async with app.run_test() as pilot:
        assert isinstance(app.screen, ResultsScreen)
        # Press 'm' to open coverage matrix
        await pilot.press("m")
        await pilot.pause()
        assert isinstance(app.screen, CoverageScreen)

        # Test export shortcut 'e' opens PathDialog
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, PathDialog)
        # Cancel dialog with escape
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, CoverageScreen)

        # Back to ResultsScreen with escape
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ResultsScreen)


@pytest.mark.asyncio
async def test_coverage_screen_small_terminal(tmp_path: Path) -> None:
    run = make_test_run()
    settings_obj = resolve_config({"theme": "textual-dark", "no_color": True})

    app = SweepApp(settings_obj, coverage_runs=[run])
    # Run test in small terminal (80x24)
    async with app.run_test(size=(80, 24)) as pilot:
        assert isinstance(app.screen, CoverageScreen)
        table = app.screen.query_one("#coverage-table", DataTable)
        assert table.row_count == 2
        await pilot.press("down")
        await pilot.pause()
        detail = str(app.screen.query_one("#coverage-detail", Static).render())
        assert "Model:" in detail
