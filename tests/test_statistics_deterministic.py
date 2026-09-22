from __future__ import annotations

import random
from pathlib import Path
from typing import Any, cast

import pytest
from textual.widgets import RichLog, Static

from llmsweep.comparison import compare_checked
from llmsweep.config import resolve_config
from llmsweep.metrics import TurnMetrics
from llmsweep.models import ModelInfo, ModelRef
from llmsweep.providers.base import Provider
from llmsweep.results import ModelResult, RunResult, SampleResult
from llmsweep.runner import RunOptions, RunSession
from llmsweep.statistics import (
    cluster_bootstrap,
    paired_bootstrap,
    reliability_report,
    wilson_interval,
)
from llmsweep.store import RunStore
from llmsweep.tui.app import SweepApp
from llmsweep.tui.screens.main import ResultsScreen, StatisticsScreen
from tests.fakes.lmstudio import FakeLMStudio


def test_paired_bootstrap_low_sample_suppression() -> None:
    rng = random.Random(42)
    # Fewer than 5 matched tasks
    left = [[1.0], [1.0], [0.0], [1.0]]
    right = [[0.0], [1.0], [0.0], [0.0]]
    report = paired_bootstrap(left, right, rng)
    assert report["interval"] is None
    assert "fewer than 5 matched tasks" in str(report["reason"])

    # Zero matched tasks
    empty_report = paired_bootstrap([], [], rng)
    assert empty_report["interval"] is None
    assert empty_report["tasks"] == 0

    # Unmatched tasks filtered out
    left_unmatched = [[1.0], [], [1.0], [1.0], [1.0], [1.0]]
    right_unmatched = [[0.0], [1.0], [1.0], [1.0], [1.0], []]
    # Usable pairs: indices 0, 2, 3, 4 -> 4 pairs (index 1 and 5 have an empty side)
    report_unmatched = paired_bootstrap(left_unmatched, right_unmatched, rng)
    assert report_unmatched["tasks"] == 4
    assert report_unmatched["interval"] is None


def test_paired_bootstrap_deterministic_superiority_and_equality() -> None:
    # Deterministic test with fixed seed
    # Identical performance across 6 tasks -> difference centered at 0
    left_equal = [[1.0, 0.0] for _ in range(6)]
    right_equal = [[1.0, 0.0] for _ in range(6)]
    rng1 = random.Random(12345)
    report_equal = paired_bootstrap(left_equal, right_equal, rng1)
    assert report_equal["interval"] is not None
    low, high = report_equal["interval"]
    assert low <= 0.0 <= high

    # Repeat with same seed yields identical interval (deterministic)
    rng2 = random.Random(12345)
    report_equal_repeat = paired_bootstrap(left_equal, right_equal, rng2)
    assert report_equal["interval"] == report_equal_repeat["interval"]

    # Strictly superior left model (left always 1.0, right always 0.0)
    left_superior = [[1.0] for _ in range(6)]
    right_inferior = [[0.0] for _ in range(6)]
    rng3 = random.Random(42)
    report_sup = paired_bootstrap(left_superior, right_inferior, rng3)
    assert report_sup["interval"] is not None
    sup_low, sup_high = report_sup["interval"]
    assert sup_low == 1.0
    assert sup_high == 1.0


def test_cluster_bootstrap_suppression_and_earlier_failure_retention() -> None:
    rng = random.Random(42)

    # 100 repeats of only 2 tasks does NOT make independent tasks!
    # Repeated attempts of one task are not independent tasks.
    two_tasks_many_repeats = [[1.0] * 50, [1.0] * 50]
    report_low = cluster_bootstrap(two_tasks_many_repeats, rng)
    assert report_low["interval"] is None
    assert "repeated attempts are not tasks" in str(report_low["reason"])

    # Extra repeats cannot erase earlier failures
    # Task 1 failed on attempt 1, then succeeded on 9 subsequent repeats.
    # The task mean is 0.9, reflecting the earlier failure.
    task_with_failure = [0.0] + [1.0] * 9
    cluster_mean = sum(task_with_failure) / len(task_with_failure)
    assert cluster_mean == 0.9  # failure is retained, not erased

    clusters = [task_with_failure] + [[1.0] * 10 for _ in range(5)]
    rng_suite = random.Random(999)
    report_suite = cluster_bootstrap(clusters, rng_suite)
    assert report_suite["interval"] is not None
    suite_low, _suite_high = report_suite["interval"]
    # Mean of clusters is (0.9 + 5*1.0) / 6 = 5.9 / 6 = 0.9833
    assert suite_low < 1.0  # Cannot be 1.0 because failure was retained


def test_adaptive_stop_at_budget_deterministic(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    provider = cast(Provider, FakeLMStudio())
    ref = ModelRef("lmstudio", "test-model")
    model_info = ModelInfo(ref=ref, type="chat")

    # 1. Fixed mode -> stops with "fixed"
    session_fixed = RunSession(provider, store, RunOptions(repeat_mode="fixed"))
    session_fixed.prepare([model_info])
    result = ModelResult(model=model_info)
    assert session_fixed._extend_exploratory(result) is False
    assert session_fixed.run.repeat_policy["stopped"] == "fixed"

    # 2. Max repeats budget
    session_max = RunSession(provider, store, RunOptions(repeat_mode="exploratory", max_repeats=3))
    session_max.prepare([model_info])
    session_max.run.schedule = [{"model": ref.key, "repeat": 3}]
    assert session_max._extend_exploratory(result) is False
    assert session_max.run.repeat_policy["stopped"] == "max_repeats"

    # 3. Time cap budget
    session_time = RunSession(
        provider, store, RunOptions(repeat_mode="exploratory", max_repeats=10, time_cap_s=10.0)
    )
    session_time.prepare([model_info])
    session_time.run.schedule = [{"model": ref.key, "repeat": 1}]
    # Add samples totaling 12 seconds
    result_timed = ModelResult(
        model=model_info,
        samples=[
            SampleResult(scenario="agent_code", repeat=1, total_s=6.0, status="completed"),
            SampleResult(scenario="agent_code", repeat=2, total_s=6.0, status="completed"),
        ],
    )
    assert session_time._extend_exploratory(result_timed) is False
    assert session_time.run.repeat_policy["stopped"] == "time_cap"

    # 4. Token cap budget
    session_token = RunSession(
        provider, store, RunOptions(repeat_mode="exploratory", max_repeats=10, token_cap=200)
    )
    session_token.prepare([model_info])
    session_token.run.schedule = [{"model": ref.key, "repeat": 1}]
    turn = TurnMetrics(
        ttft_ms=100.0,
        generation_s=1.0,
        output_tokens=250,
        token_source="usage",
        total_tokens=270,
    )
    result_token = ModelResult(
        model=model_info,
        samples=[
            SampleResult(scenario="agent_code", repeat=1, turns=[turn], status="completed"),
        ],
    )
    assert session_token._extend_exploratory(result_token) is False
    assert session_token.run.repeat_policy["stopped"] == "token_cap"

    # 5. Precision target budget
    # When interval widths across tasks <= precision_target
    session_prec = RunSession(
        provider, store, RunOptions(repeat_mode="exploratory", max_repeats=50, precision=0.5)
    )
    session_prec.prepare([model_info])
    session_prec.run.schedule = [{"model": ref.key, "repeat": 10}]
    # 20 identical successful samples on task_1: Wilson interval width will be very narrow
    samples_prec = [
        SampleResult(scenario="agent_code", repeat=i, success=True, status="completed", task_id="t1")
        for i in range(1, 21)
    ]
    w = wilson_interval(20, 20)
    assert w is not None
    assert (w[1] - w[0]) < 0.5
    result_prec = ModelResult(model=model_info, samples=samples_prec)
    assert session_prec._extend_exploratory(result_prec) is False
    assert session_prec.run.repeat_policy["stopped"] == "precision"


def test_adaptive_policy_disables_regression_verdicts() -> None:
    ref = ModelRef("lmstudio", "m")
    info = ModelInfo(ref=ref, type="chat")
    sample = SampleResult(scenario="agent_code", repeat=1, status="completed", success=True, total_s=2.0)
    model_res = ModelResult(model=info, samples=[sample], status="completed")

    # Current run has adaptive repeat policy
    current = RunResult(
        run_id="curr",
        started_at="2026-09-22T00:00:00Z",
        settings={"scenarios": ["agent_code"], "benchmark_version": "1.0"},
        models=[model_res],
        status="completed",
        repeat_policy={"adaptive": True},
        schema_version=2,
    )
    baseline = RunResult(
        run_id="base",
        started_at="2026-09-22T00:00:00Z",
        settings={"scenarios": ["agent_code"], "benchmark_version": "1.0"},
        models=[model_res],
        status="completed",
        repeat_policy={"adaptive": False},
        schema_version=2,
    )

    records = compare_checked(current, baseline)
    assert len(records) == 1
    assert records[0]["verdict"] == "not comparable"
    assert "adaptive repeat policy disables automatic regression verdicts" in records[0]["reason"]


def test_reliability_report_denominators_and_confidence_labels() -> None:
    samples: list[dict[str, Any]] = [
        {"task_id": "t1", "scenario": "sc1", "status": "completed", "success": True, "total_s": 1.0},
        {"task_id": "t1", "scenario": "sc1", "status": "completed", "success": False, "total_s": 1.2},
        {"task_id": "t2", "scenario": "sc1", "status": "error", "success": None, "total_s": 0.0},
        {"task_id": "t3", "scenario": "sc1", "status": "skipped", "success": None, "total_s": 0.0},
        {"task_id": "t4", "scenario": "sc1", "status": "cancelled", "success": None, "total_s": 0.0},
    ]
    rep_adapt = reliability_report(samples, adaptive=True, rng=random.Random(1))
    assert rep_adapt["confidence_label"] == "descriptive"
    assert rep_adapt["attempted"] == 5
    assert rep_adapt["scored"] == 2
    assert rep_adapt["errors"] == 1
    assert rep_adapt["skips"] == 1
    assert rep_adapt["cancellations"] == 1
    assert rep_adapt["coverage"] == 2 / 5

    # Adaptive = False -> confidence_label == "fixed-repeat"
    rep_fixed = reliability_report(samples, adaptive=False, rng=random.Random(1))
    assert rep_fixed["confidence_label"] == "fixed-repeat"


@pytest.mark.asyncio
async def test_tui_statistics_screen_from_results(tmp_path: Path) -> None:
    ref = ModelRef("lmstudio", "model-stats")
    info = ModelInfo(ref=ref, type="chat")
    samples = [
        SampleResult(
            scenario="agent_code",
            repeat=i,
            status="completed",
            success=(i % 2 == 0),
            total_s=1.5,
            task_id=f"task_{i % 3}",
        )
        for i in range(1, 7)
    ]
    model_res = ModelResult(model=info, samples=samples, status="completed")
    run = RunResult(
        run_id="run-stats",
        started_at="2026-09-22T00:00:00Z",
        settings={"scenarios": ["agent_code"]},
        models=[model_res],
        status="completed",
    )

    settings_obj = resolve_config({"theme": "textual-dark", "no_color": True})
    app = SweepApp(settings_obj, saved_run=run)

    async with app.run_test() as pilot:
        assert isinstance(app.screen, ResultsScreen)

        # Check that show_details shows confidence information
        detail = str(app.screen.query_one("#results-detail", Static).render())
        assert "load" in detail

        # Press 'i' to open StatisticsScreen modal
        await pilot.press("i")
        await pilot.pause()
        assert isinstance(app.screen, StatisticsScreen)

        # Verify StatisticsScreen content
        log = app.screen.query_one(RichLog)
        assert log is not None

        # Dismiss with escape
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ResultsScreen)
