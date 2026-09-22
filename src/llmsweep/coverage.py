"""Benchmark coverage and results matrix computation.

Renderers only display this computed data. Calculations live outside renderers.
Missing results never appear as zero scores; denominators and outcome labels are explicit.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from pathlib import Path
from typing import Any

from llmsweep.results import RunResult
from llmsweep.security import Redactor
from llmsweep.store import atomic_write, canonical_json

DEFAULT_BENCHMARK_CATEGORIES: dict[str, str] = {
    "weather": "tools",
    "agent-code": "agent",
    "agent_code": "agent",
    "codegen": "coding",
    "code-edge": "coding",
    "code_edge": "coding",
    "multi-file": "coding",
    "multi_file": "coding",
    "repo-issue": "coding",
    "repo_issue": "coding",
    "constraint-plan": "planning",
    "constraint_plan": "planning",
    "knowledge-cal": "knowledge",
    "knowledge_cal": "knowledge",
}


def resolve_category(benchmark: str, samples: list[Any]) -> str:
    """Resolve benchmark category from samples or built-in defaults."""
    for s in samples:
        cat = getattr(s, "category", None)
        if cat:
            return str(cat)
    normalized = benchmark.lower().replace("_", "-")
    return DEFAULT_BENCHMARK_CATEGORIES.get(
        benchmark, DEFAULT_BENCHMARK_CATEGORIES.get(normalized, "general")
    )


def build_coverage_matrix(
    runs: list[RunResult],
    *,
    run_id: str | None = None,
    model: str | None = None,
    category: str | None = None,
    configuration: str | None = None,
) -> dict[str, Any]:
    """Compute model-by-benchmark matrix, coverage percentages, and task details."""
    filtered_runs = runs
    if run_id:
        filtered_runs = [r for r in filtered_runs if run_id.casefold() in r.run_id.casefold()]
    if configuration:
        filtered_runs = [
            r
            for r in filtered_runs
            if r.fingerprint and configuration.casefold() in r.fingerprint.casefold()
        ]

    # Collect models and benchmarks across selected runs
    model_keys: set[str] = set()
    all_benchmarks: set[str] = set()
    benchmark_categories: dict[str, str] = {}
    planned_per_run_bench: dict[tuple[str, str], int] = {}

    for r in filtered_runs:
        repeats_setting = int(r.settings.get("repeats", 1) or 1)
        planned_scenarios = list(r.settings.get("scenarios") or [])
        for sc in planned_scenarios:
            all_benchmarks.add(sc)
            planned_per_run_bench[(r.run_id, sc)] = repeats_setting

        for m in r.models:
            m_key = m.model.ref.key
            if model and model.casefold() not in m_key.casefold():
                continue
            model_keys.add(m_key)
            for s in m.samples:
                bench = s.suite_id or s.scenario
                all_benchmarks.add(bench)
                if bench not in benchmark_categories:
                    benchmark_categories[bench] = resolve_category(bench, [s])

    for bench in all_benchmarks:
        if bench not in benchmark_categories:
            benchmark_categories[bench] = resolve_category(bench, [])

    # Filter categories if requested
    if category:
        selected_benchmarks = sorted(
            b
            for b in all_benchmarks
            if category.casefold() in benchmark_categories.get(b, "").casefold()
            or category.casefold() in b.casefold()
        )
    else:
        selected_benchmarks = sorted(all_benchmarks)

    selected_models = sorted(model_keys)

    cells: dict[str, dict[str, Any]] = {}
    plain_rows: list[dict[str, Any]] = []

    total_cells = 0
    complete_cells = 0
    incomplete_cells = 0
    unrun_cells = 0
    unavailable_cells = 0

    for m_key in selected_models:
        for bench in selected_benchmarks:
            total_cells += 1
            # Gather samples for this model and benchmark across the filtered runs
            bench_samples = []
            matching_run_ids = []
            planned_count = 0

            for r in filtered_runs:
                # Determine planned samples for this benchmark in this run
                default_planned = planned_per_run_bench.get(
                    (r.run_id, bench), int(r.settings.get("repeats", 1) or 1)
                )
                for m in r.models:
                    if m.model.ref.key == m_key:
                        matching_run_ids.append(r.run_id)
                        samples = [s for s in m.samples if (s.suite_id or s.scenario) == bench]
                        bench_samples.extend(samples)
                        planned_count += max(len(samples), default_planned)

            completed_samples = [s for s in bench_samples if s.status == "completed"]
            scored_samples = [s for s in completed_samples if s.success is not None]
            successes = sum(1 for s in scored_samples if s.success is True)
            failures = sum(
                1
                for s in bench_samples
                if s.status in {"error", "completed"} and s.success is False
            )
            errors = sum(1 for s in bench_samples if s.status == "error")
            skipped = sum(1 for s in bench_samples if s.status == "skipped")
            cancelled = sum(1 for s in bench_samples if s.status in {"cancelled", "interrupted"})

            # Task level grouping
            task_details: dict[str, dict[str, Any]] = defaultdict(
                lambda: {
                    "completed": 0,
                    "failed": 0,
                    "errors": 0,
                    "skipped": 0,
                    "cancelled": 0,
                    "successes": 0,
                    "durations": [],
                }
            )
            for s in bench_samples:
                t_id = s.task_id or s.scenario
                td = task_details[t_id]
                if s.status == "completed":
                    td["completed"] += 1
                    if s.success is True:
                        td["successes"] += 1
                    elif s.success is False:
                        td["failed"] += 1
                    if s.total_s > 0:
                        td["durations"].append(float(s.total_s))
                elif s.status == "error":
                    td["errors"] += 1
                elif s.status == "skipped":
                    td["skipped"] += 1
                elif s.status in {"cancelled", "interrupted"}:
                    td["cancelled"] += 1

            tasks_list = []
            for t_id, td in task_details.items():
                dur_list = td["durations"]
                t_scored = td["successes"] + td["failed"]
                tasks_list.append(
                    {
                        "task_id": t_id,
                        "completed": td["completed"],
                        "successes": td["successes"],
                        "failed": td["failed"],
                        "errors": td["errors"],
                        "skipped": td["skipped"],
                        "cancelled": td["cancelled"],
                        "success_rate": (td["successes"] / t_scored) if t_scored else None,
                        "mean_duration_s": (sum(dur_list) / len(dur_list)) if dur_list else None,
                    }
                )

            # Determine cell status & label
            if planned_count == 0:
                planned_count = len(bench_samples)

            if not bench_samples:
                status = "unrun"
                unrun_cells += 1
                success_rate = None
                label = f"unrun (0/{planned_count})" if planned_count else "unrun"
                coverage = 0.0
            elif not scored_samples:
                status = "unavailable"
                unavailable_cells += 1
                success_rate = None
                label = f"unavail (0/{planned_count})"
                coverage = len(completed_samples) / planned_count if planned_count else 0.0
            elif len(completed_samples) < planned_count:
                status = "incomplete"
                incomplete_cells += 1
                success_rate = successes / len(scored_samples)
                label = f"{success_rate:.0%} ({len(completed_samples)}/{planned_count} inc)"
                coverage = len(completed_samples) / planned_count
            else:
                status = "complete"
                complete_cells += 1
                success_rate = successes / len(scored_samples)
                label = f"{success_rate:.0%} ({len(completed_samples)}/{planned_count})"
                coverage = 1.0

            cell_data = {
                "model": m_key,
                "benchmark": bench,
                "category": benchmark_categories.get(bench, "general"),
                "status": status,
                "label": label,
                "success_rate": success_rate,
                "completed": len(completed_samples),
                "planned": planned_count,
                "coverage": coverage,
                "successes": successes,
                "failures": failures,
                "errors": errors,
                "skipped": skipped,
                "cancelled": cancelled,
                "tasks": tasks_list,
                "run_ids": matching_run_ids,
            }
            cells[f"{m_key}::{bench}"] = cell_data

            plain_rows.append(
                {
                    "model": m_key,
                    "benchmark": bench,
                    "category": benchmark_categories.get(bench, "general"),
                    "status": status,
                    "label": label,
                    "success_rate": success_rate,
                    "completed": len(completed_samples),
                    "planned": planned_count,
                    "coverage": coverage,
                    "failures": failures,
                    "errors": errors,
                    "skipped": skipped,
                    "cancelled": cancelled,
                }
            )

    overall_coverage = (
        sum(r["coverage"] for r in plain_rows) / len(plain_rows) if plain_rows else 0.0
    )

    summary = {
        "total_models": len(selected_models),
        "total_benchmarks": len(selected_benchmarks),
        "total_cells": total_cells,
        "complete_cells": complete_cells,
        "incomplete_cells": incomplete_cells,
        "unrun_cells": unrun_cells,
        "unavailable_cells": unavailable_cells,
        "overall_coverage": overall_coverage,
    }

    return {
        "models": selected_models,
        "benchmarks": selected_benchmarks,
        "categories": benchmark_categories,
        "cells": cells,
        "rows": plain_rows,
        "summary": summary,
        "notes": [
            "Missing results never appear as zero scores.",
            "Unrun and unavailable cells display distinct labels with explicit denominators.",
            "Coverage tracks completed samples against planned samples.",
        ],
    }


def coverage_to_json(matrix_view: dict[str, Any]) -> str:
    """Serialize benchmark coverage matrix data to canonical JSON."""
    return canonical_json(matrix_view)


def coverage_to_csv(matrix_view: dict[str, Any]) -> str:
    """Export coverage plain rows and matrix breakdown to CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "model",
            "benchmark",
            "category",
            "status",
            "label",
            "success_rate",
            "completed",
            "planned",
            "coverage",
            "failures",
            "errors",
            "skipped",
            "cancelled",
        ]
    )
    for row in matrix_view.get("rows", []):
        rate = row.get("success_rate")
        cov = row.get("coverage")
        writer.writerow(
            [
                row.get("model"),
                row.get("benchmark"),
                row.get("category"),
                row.get("status"),
                row.get("label"),
                f"{rate:.4f}" if rate is not None else "",
                row.get("completed"),
                row.get("planned"),
                f"{cov:.4f}" if cov is not None else "",
                row.get("failures"),
                row.get("errors"),
                row.get("skipped"),
                row.get("cancelled"),
            ]
        )
    return output.getvalue()


def coverage_to_markdown(matrix_view: dict[str, Any]) -> str:
    """Export benchmark coverage results matrix to Markdown."""
    lines = ["# Benchmark Coverage and Results Dashboard\n"]
    summary = matrix_view.get("summary", {})
    lines.append("### Summary")
    m_count = summary.get("total_models", 0)
    b_count = summary.get("total_benchmarks", 0)
    lines.append(f"- Models: {m_count} · Benchmarks: {b_count}")
    lines.append(
        f"- Complete: {summary.get('complete_cells', 0)} · "
        f"Incomplete: {summary.get('incomplete_cells', 0)} · "
        f"Unrun: {summary.get('unrun_cells', 0)} · "
        f"Unavailable: {summary.get('unavailable_cells', 0)}"
    )
    cov = summary.get("overall_coverage")
    cov_str = f"{cov:.1%}" if cov is not None else "n/a"
    lines.append(f"- Overall Coverage: {cov_str}\n")

    models = matrix_view.get("models", [])
    benchmarks = matrix_view.get("benchmarks", [])
    cells = matrix_view.get("cells", {})

    lines.append("## Results Matrix\n")
    if models and benchmarks:
        header = "| Model | " + " | ".join(benchmarks) + " |"
        sep = "| --- | " + " | ".join(["---"] * len(benchmarks)) + " |"
        lines.append(header)
        lines.append(sep)
        for m in models:
            row_cells = [m]
            for b in benchmarks:
                cell = cells.get(f"{m}::{b}", {})
                row_cells.append(cell.get("label", "unrun"))
            lines.append("| " + " | ".join(row_cells) + " |")
        lines.append("")
    else:
        lines.append("*No matrix cells matching filters.*\n")

    lines.append("## Detailed Coverage Table\n")
    lines.append(
        "| Model | Benchmark | Category | Status | Pass Rate | Completed | Planned | Coverage |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in matrix_view.get("rows", []):
        rate = row.get("success_rate")
        rate_str = f"{rate:.1%}" if rate is not None else "n/a"
        cov = row.get("coverage")
        cov_str = f"{cov:.0%}" if cov is not None else "n/a"
        lines.append(
            f"| {row['model']} | {row['benchmark']} | {row['category']} | {row['status']} | "
            f"{rate_str} | {row['completed']} | {row['planned']} | {cov_str} |"
        )
    lines.append("")

    return "\n".join(lines)


def export_coverage(
    matrix_view: dict[str, Any],
    *,
    json_path: Path | None = None,
    csv_path: Path | None = None,
    markdown_path: Path | None = None,
    redact: Redactor | None = None,
) -> None:
    """Atomically write coverage matrix outputs with optional redaction."""
    clean = redact or Redactor()
    for path, text in [
        (json_path, coverage_to_json(matrix_view)),
        (csv_path, coverage_to_csv(matrix_view)),
        (markdown_path, coverage_to_markdown(matrix_view)),
    ]:
        if path is not None:
            atomic_write(path, clean(text))
