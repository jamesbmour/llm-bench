"""Comparison eligibility and dashboard view models. Renderers only display this output."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from pathlib import Path
from typing import Any

from llmsweep.metrics import regression_pct
from llmsweep.results import RunResult, compare_runs
from llmsweep.security import Redactor
from llmsweep.statistics import wilson_interval
from llmsweep.store import atomic_write, canonical_json

_SETTING_KEYS = ("scenarios", "max_tokens", "max_turns", "task", "no_warmup", "benchmark_version")


def _digest_map(run: RunResult) -> dict[tuple[str, str], str | None]:
    found: dict[tuple[str, str], str | None] = {}
    for model in run.models:
        for sample in model.samples:
            key = (sample.suite_id or sample.scenario, sample.task_id or sample.scenario)
            found[key] = sample.content_digest
    return found


def _timing_context(run: RunResult) -> dict[str, Any] | None:
    provenance = run.provenance or {}
    if not provenance:
        return None
    return {
        "hardware": provenance.get("hardware"),
        "os": provenance.get("os"),
        "execution_policy": provenance.get("execution_policy"),
        "server": (provenance.get("inference_server") or {}).get("name"),
        "requested": provenance.get("requested"),
    }


def compare_checked(
    current: RunResult, baseline: RunResult, tok_threshold: float = 5, ttft_threshold: float = 5
) -> list[dict[str, Any]]:
    if current.schema_version < 2 or baseline.schema_version < 2:
        return compare_runs(current, baseline, tok_threshold, ttft_threshold)
    records: list[dict[str, Any]] = []
    if current.diagnostic or baseline.diagnostic:
        return [
            {
                "model": model.model.ref.key,
                "scenario": sample.scenario,
                "verdict": "not comparable",
                "reason": "diagnostic rerun",
                "quality_only": False,
            }
            for model in current.models
            for sample in model.samples
        ]
    adaptive = bool(
        (current.repeat_policy or {}).get("adaptive")
        or (baseline.repeat_policy or {}).get("adaptive")
    )
    digests_now = _digest_map(current)
    digests_before = _digest_map(baseline)
    timing_now = _timing_context(current)
    timing_before = _timing_context(baseline)
    timing_ok = timing_now is not None and timing_now == timing_before
    previous = {model.model.ref: model for model in baseline.models}
    settings_match = all(
        current.settings.get(key) == baseline.settings.get(key) for key in _SETTING_KEYS
    )
    for model in current.models:
        prior = previous.get(model.model.ref)
        for scenario, now in model.rollups().items():
            reason = None
            quality_only = False
            before = prior.rollups().get(scenario) if prior else None
            task_keys = [key for key in digests_now if key[0] == scenario]
            if any(digests_now.get(key) != digests_before.get(key) for key in task_keys):
                reason = "task content digest mismatch"
            elif not before:
                reason = "no matching provider/model/scenario baseline"
            elif not settings_match:
                reason = "benchmark settings differ"
            elif adaptive:
                reason = "adaptive repeat policy disables automatic regression verdicts"
            elif model.contended or (prior and prior.contended):
                reason = "contended measurements"
            elif not now["complete"] or not before["complete"]:
                reason = "incomplete measurements"
            elif now["token_sources"] != before["token_sources"] or len(now["token_sources"]) != 1:
                reason = "token sources differ or are mixed"
            elif not timing_ok:
                reason = "timing incompatible; quality-only comparison is available"
                quality_only = True
            record: dict[str, Any] = {
                "model": model.model.ref.key,
                "scenario": scenario,
                "verdict": "not comparable",
                "reason": reason,
                "quality_only": quality_only,
                "mode": "regression" if prior else "exploration",
            }
            if reason is None and before:
                speed = regression_pct(
                    now["tok_s"]["mean"], before["tok_s"]["mean"], higher_better=True
                )
                ttft = regression_pct(
                    now["ttft_ms"]["mean"], before["ttft_ms"]["mean"], higher_better=False
                )
                regressed = (speed is not None and speed > tok_threshold) or (
                    ttft is not None and ttft > ttft_threshold
                )
                record.update(
                    tok_s_regression_pct=speed,
                    ttft_regression_pct=ttft,
                    verdict="regression" if regressed else "within threshold",
                )
                if speed is None and ttft is None:
                    record.update(verdict="not comparable", reason="no valid timing baseline")
            records.append(record)
    return records


def _sample_rows(run: RunResult) -> list[dict[str, Any]]:
    rows = []
    for model in run.models:
        for sample in model.samples:
            rows.append(
                {
                    "run_id": run.run_id,
                    "target": model.model.ref.key,
                    "target_kind": sample.target_kind or model.target_kind,
                    "benchmark": sample.suite_id or sample.scenario,
                    "task_id": sample.task_id or sample.scenario,
                    "configuration": run.fingerprint,
                    "status": sample.status,
                    "success": sample.success,
                    "total_s": sample.total_s,
                    "tok_s": sample.tok_s,
                    "failure_category": sample.failure_category,
                    "diagnostic": run.diagnostic or sample.diagnostic,
                }
            )
    return rows


def build_views(
    runs: list[RunResult],
    *,
    target: str | None = None,
    benchmark: str | None = None,
    task: str | None = None,
    configuration: str | None = None,
) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        for row in _sample_rows(run):
            if target and target.casefold() not in row["target"].casefold():
                continue
            if benchmark and benchmark.casefold() not in row["benchmark"].casefold():
                continue
            if (
                task
                and task.casefold() not in row["task_id"].casefold()
                and task.casefold() not in row["benchmark"].casefold()
            ):
                continue
            if configuration and (
                row["configuration"] is None
                or configuration.casefold() not in row["configuration"].casefold()
            ):
                continue
            grouped[(row["run_id"], row["target"], row["benchmark"], row["target_kind"])].append(
                row
            )
    points = []
    for (run_id, target_key, suite, kind), rows in grouped.items():
        scored = [
            row for row in rows if row["success"] is not None and row["status"] == "completed"
        ]
        successes = sum(1 for row in scored if row["success"] is True)
        durations = [
            float(row["total_s"]) for row in scored if isinstance(row["total_s"], (int, float))
        ]
        speeds = [float(row["tok_s"]) for row in scored if isinstance(row["tok_s"], (int, float))]
        fingerprints = {row["configuration"] for row in rows}
        interval = wilson_interval(successes, len(scored)) if scored else None
        coverage = (len(scored) / len(rows)) if rows else None
        incomplete = any(row["status"] not in {"completed", "skipped"} for row in rows) or (
            len(scored) < len(rows)
        )
        dur_val = (sum(durations) / len(durations)) if durations else None
        speed_val = (sum(speeds) / len(speeds)) if speeds else None
        rate_val = (successes / len(scored)) if scored else None
        unavailable = []
        if rate_val is None:
            unavailable.append("success_rate")
        if dur_val is None:
            unavailable.append("task_duration_s")
        if speed_val is None:
            unavailable.append("tok_s")

        points.append(
            {
                "run_id": run_id,
                "target": target_key,
                "target_kind": kind,
                "benchmark": suite,
                "samples": len(rows),
                "scored": len(scored),
                "failures": sum(
                    1
                    for row in rows
                    if row["status"] in {"error", "completed"} and row["success"] is False
                ),
                "errors": sum(1 for row in rows if row["status"] == "error"),
                "skipped": sum(1 for row in rows if row["status"] == "skipped"),
                "cancelled": sum(
                    1 for row in rows if row["status"] in {"cancelled", "interrupted"}
                ),
                "success_rate": rate_val,
                "confidence_interval": list(interval) if interval else None,
                "task_duration_s": dur_val,
                "tok_s": speed_val,
                "coverage": coverage,
                "incomplete": incomplete,
                "timing_comparable": len(fingerprints) <= 1
                and all(item is not None for item in fingerprints)
                and not any(row.get("diagnostic") for row in rows),
                "unavailable_metrics": unavailable,
            }
        )

    def table(metric: str) -> list[dict[str, Any]]:
        return [
            {
                "run_id": point["run_id"],
                "target": point["target"],
                "target_kind": point["target_kind"],
                "benchmark": point["benchmark"],
                "success_rate": point["success_rate"],
                "confidence_interval": point["confidence_interval"],
                metric: point[metric],
                "samples": point["samples"],
                "scored": point["scored"],
                "failures": point["failures"],
                "coverage": point["coverage"],
                "incomplete": point["incomplete"],
                "timing_comparable": point["timing_comparable"],
                "unavailable_metrics": point["unavailable_metrics"],
            }
            for point in points
        ]

    return {
        "points": points,
        "success_vs_duration": table("task_duration_s"),
        "success_vs_throughput": table("tok_s"),
        "quality_only": [point for point in points if point["success_rate"] is not None],
        "notes": [
            "Timing rankings require matching provenance. "
            "Quality rows remain available when timing does not.",
            "Model-harness rows and whole-agent rows use target_kind and are not one ranking.",
        ],
    }


def build_ascii_plot(
    points: list[dict[str, Any]],
    metric: str,
    *,
    width: int = 60,
    height: int = 12,
) -> str:
    metric_label = "Duration (s)" if metric == "task_duration_s" else "Throughput (tok/s)"
    if not points:
        return f"  [No data to plot for {metric_label}]\n"

    valid = [p for p in points if p.get(metric) is not None and p.get("success_rate") is not None]
    if not valid:
        return f"  [Unavailable metrics: no plottable data for {metric_label}]\n"

    x_vals = [float(p[metric]) for p in valid]
    min_x = min(x_vals)
    max_x = max(x_vals)
    if min_x == max_x:
        min_x = max(0.0, min_x - 1.0)
        max_x = max_x + 1.0 if max_x > 0 else 1.0

    min_y = 0.0
    max_y = 1.0

    plot_w = max(20, min(width - 14, 80))
    plot_h = max(5, min(height - 4, 15))

    grid = [[" " for _ in range(plot_w)] for _ in range(plot_h)]
    markers: list[tuple[str, dict[str, Any]]] = []

    for i, p in enumerate(valid):
        marker = str(i + 1) if i < 9 else chr(ord("A") + i - 9)
        markers.append((marker, p))
        x = float(p[metric])
        y = float(p["success_rate"])
        col = round((x - min_x) / (max_x - min_x) * (plot_w - 1))
        col = max(0, min(plot_w - 1, col))
        row = round((max_y - y) / (max_y - min_y) * (plot_h - 1))
        row = max(0, min(plot_h - 1, row))
        grid[row][col] = marker
    lines = []
    lines.append(f"  Success Rate vs {metric_label}")
    for r in range(plot_h):
        if r == 0:
            y_label = "100% |"
        elif r == plot_h // 2:
            y_label = " 50% |"
        elif r == plot_h - 1:
            y_label = "  0% |"
        else:
            y_label = "     |"
        lines.append(f"{y_label}{''.join(grid[r])}")

    lines.append("     +" + "-" * plot_w)
    x_min_str = f"{min_x:.1f}"
    x_max_str = f"{max_x:.1f}"
    spacing = plot_w - len(x_min_str) - len(x_max_str)
    if spacing > 0:
        lines.append("      " + x_min_str + " " * spacing + x_max_str)
    else:
        lines.append(f"      {x_min_str} .. {x_max_str}")
    lines.append(f"      {metric_label}")
    lines.append("")
    lines.append("  Legend:")
    for marker, p in markers:
        ci = p.get("confidence_interval")
        ci_str = f" [95% CI: {ci[0]:.1%}-{ci[1]:.1%}]" if ci else ""
        timing_str = " (timing incompatible)" if not p.get("timing_comparable") else ""
        cov_str = f" coverage: {p['coverage']:.0%}" if p.get("coverage") is not None else ""
        entry = (
            f"   [{marker}] {p['target']} ({p['benchmark']}) — "
            f"success: {p['success_rate']:.1%}{ci_str}, {metric_label}: {p[metric]:.2f}"
            f"{timing_str}{cov_str}"
        )
        lines.append(entry)
    unavail = [p for p in points if p.get(metric) is None or p.get("success_rate") is None]
    if unavail:
        lines.append("  Omitted (metrics unavailable):")
        for p in unavail:
            lines.append(
                f"   · {p['target']} ({p['benchmark']}) — {', '.join(p['unavailable_metrics'])}"
            )

    return "\n".join(lines)


def comparison_to_json(view: dict[str, Any]) -> str:
    """Serialize comparison view data to canonical JSON."""
    return canonical_json(view)


def comparison_to_csv(view: dict[str, Any]) -> str:
    """Export comparison view points to CSV format."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "run_id",
            "target",
            "target_kind",
            "benchmark",
            "samples",
            "scored",
            "failures",
            "coverage",
            "incomplete",
            "success_rate",
            "ci_95_low",
            "ci_95_high",
            "task_duration_s",
            "tok_s",
            "timing_comparable",
        ]
    )
    for p in view.get("points", []):
        ci = p.get("confidence_interval")
        writer.writerow(
            [
                p.get("run_id"),
                p.get("target"),
                p.get("target_kind"),
                p.get("benchmark"),
                p.get("samples"),
                p.get("scored"),
                p.get("failures"),
                f"{p['coverage']:.4f}" if p.get("coverage") is not None else "",
                p.get("incomplete"),
                f"{p['success_rate']:.4f}" if p.get("success_rate") is not None else "",
                f"{ci[0]:.4f}" if ci else "",
                f"{ci[1]:.4f}" if ci else "",
                f"{p['task_duration_s']:.4f}" if p.get("task_duration_s") is not None else "",
                f"{p['tok_s']:.4f}" if p.get("tok_s") is not None else "",
                p.get("timing_comparable"),
            ]
        )
    return output.getvalue()


def comparison_to_markdown(view: dict[str, Any]) -> str:
    """Export comparison view tables and plots to Markdown."""
    lines = ["# Quality-Speed Comparison Report\n"]
    if view.get("notes"):
        lines.append("### Notes")
        for note in view["notes"]:
            lines.append(f"- {note}")
        lines.append("")

    lines.append("## Success vs Task Duration\n")
    header_dur = (
        "| Run | Target | Kind | Benchmark | Success Rate | 95% CI | Duration (s) | "
        "Samples | Scored | Coverage | Timing Comparable |"
    )
    lines.append(header_dur)
    lines.append(
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )
    for row in view.get("success_vs_duration", []):
        ci = row.get("confidence_interval")
        ci_str = f"[{ci[0]:.1%}, {ci[1]:.1%}]" if ci else "n/a"
        rate_str = f"{row['success_rate']:.1%}" if row.get("success_rate") is not None else "n/a"
        dur_val = row.get("task_duration_s")
        dur_str = f"{dur_val:.2f}" if dur_val is not None else "n/a"
        cov_str = f"{row['coverage']:.0%}" if row.get("coverage") is not None else "n/a"
        timing_str = "yes" if row.get("timing_comparable") else "no (differing settings)"
        lines.append(
            f"| {row['run_id']} | {row['target']} | {row['target_kind']} | {row['benchmark']} |"
            f" {rate_str} | {ci_str} | {dur_str} | {row['samples']} | {row['scored']} |"
            f" {cov_str} | {timing_str} |"
        )
    lines.append("")

    dur_plot = build_ascii_plot(view.get("points", []), "task_duration_s")
    lines.append("```text")
    lines.append(dur_plot)
    lines.append("```\n")

    lines.append("## Success vs Throughput (tok/s)\n")
    header_tok = (
        "| Run | Target | Kind | Benchmark | Success Rate | 95% CI | tok/s | "
        "Samples | Scored | Coverage | Timing Comparable |"
    )
    lines.append(header_tok)
    lines.append(
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )
    for row in view.get("success_vs_throughput", []):
        ci = row.get("confidence_interval")
        ci_str = f"[{ci[0]:.1%}, {ci[1]:.1%}]" if ci else "n/a"
        rate_str = f"{row['success_rate']:.1%}" if row.get("success_rate") is not None else "n/a"
        tok_val = row.get("tok_s")
        tok_str = f"{tok_val:.2f}" if tok_val is not None else "n/a"
        cov_str = f"{row['coverage']:.0%}" if row.get("coverage") is not None else "n/a"
        timing_str = "yes" if row.get("timing_comparable") else "no (differing settings)"
        lines.append(
            f"| {row['run_id']} | {row['target']} | {row['target_kind']} | {row['benchmark']} |"
            f" {rate_str} | {ci_str} | {tok_str} | {row['samples']} | {row['scored']} |"
            f" {cov_str} | {timing_str} |"
        )
    lines.append("")

    tok_plot = build_ascii_plot(view.get("points", []), "tok_s")
    lines.append("```text")
    lines.append(tok_plot)
    lines.append("```\n")

    return "\n".join(lines)


def export_comparison(
    view: dict[str, Any],
    *,
    json_path: Path | None = None,
    csv_path: Path | None = None,
    markdown_path: Path | None = None,
    redact: Redactor | None = None,
) -> None:
    """Atomically write comparison view outputs with optional redaction."""
    clean = redact or Redactor()
    for path, text in [
        (json_path, comparison_to_json(view)),
        (csv_path, comparison_to_csv(view)),
        (markdown_path, comparison_to_markdown(view)),
    ]:
        if path is not None:
            atomic_write(path, clean(text))
