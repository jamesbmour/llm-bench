"""Repeat reliability and task-cluster uncertainty.

Renderers print these results; they do not calculate them. Repeated attempts of
one task are not independent tasks. Suite intervals require at least five tasks.
"""

from __future__ import annotations

import math
import random
from typing import Any

Z_95 = 1.959963984540054
MIN_SUITE_TASKS = 5


def wilson_interval(successes: int, trials: int, *, z: float = Z_95) -> tuple[float, float] | None:
    """95% Wilson interval for a repeated binary outcome. None when there are no trials."""
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError("successes must lie between 0 and trials")
    if trials == 0:
        return None
    phat = successes / trials
    z2 = z * z
    denom = 1.0 + z2 / trials
    center = (phat + z2 / (2.0 * trials)) / denom
    margin = z * math.sqrt(phat * (1.0 - phat) / trials + z2 / (4.0 * trials * trials)) / denom
    low = max(0.0, center - margin)
    high = min(1.0, center + margin)
    return (low, high)


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        raise ValueError("empty sample")
    index = round(fraction * (len(sorted_values) - 1))
    return sorted_values[index]


def cluster_bootstrap(
    clusters: list[list[float]],
    rng: random.Random,
    *,
    draws: int = 1000,
    min_clusters: int = MIN_SUITE_TASKS,
) -> dict[str, Any]:
    """Resample task-level clusters. Insufficient task counts suppress the interval."""
    usable = [cluster for cluster in clusters if cluster]
    report: dict[str, Any] = {
        "method": "task-cluster bootstrap",
        "tasks": len(usable),
        "draws": draws,
        "interval": None,
        "reason": None,
    }
    if len(usable) < min_clusters:
        report["reason"] = (
            f"fewer than {min_clusters} distinct tasks; repeated attempts are not tasks"
        )
        return report
    means: list[float] = []
    for _ in range(draws):
        picked = [rng.choice(usable) for _ in usable]
        task_means = [sum(cluster) / len(cluster) for cluster in picked]
        means.append(sum(task_means) / len(task_means))
    means.sort()
    report["interval"] = [_percentile(means, 0.025), _percentile(means, 0.975)]
    return report


def paired_bootstrap(
    left: list[list[float]],
    right: list[list[float]],
    rng: random.Random,
    *,
    draws: int = 1000,
    min_clusters: int = MIN_SUITE_TASKS,
) -> dict[str, Any]:
    """Resample matched task pairs together and report the difference of task means."""
    pairs = [(a, b) for a, b in zip(left, right, strict=False) if a and b]
    report: dict[str, Any] = {
        "method": "paired task-cluster bootstrap",
        "tasks": len(pairs),
        "draws": draws,
        "interval": None,
        "reason": None,
    }
    if len(pairs) < min_clusters:
        report["reason"] = f"fewer than {min_clusters} matched tasks"
        return report
    diffs: list[float] = []
    for _ in range(draws):
        picked = [rng.choice(pairs) for _ in pairs]
        difference = [sum(a) / len(a) - sum(b) / len(b) for a, b in picked]
        diffs.append(sum(difference) / len(difference))
    diffs.sort()
    report["interval"] = [_percentile(diffs, 0.025), _percentile(diffs, 0.975)]
    return report


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def reliability_report(
    samples: list[dict[str, Any]], *, adaptive: bool, rng: random.Random
) -> dict[str, Any]:
    """Summarize denominators and confidence for one model's samples."""
    by_task: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        by_task.setdefault(str(sample.get("task_id") or sample.get("scenario") or ""), []).append(
            sample
        )
    tasks: list[dict[str, Any]] = []
    success_clusters: list[list[float]] = []
    duration_clusters: list[list[float]] = []
    for task_id, rows in by_task.items():
        scored = [
            row
            for row in rows
            if row.get("success") is not None and row.get("status") == "completed"
        ]
        successes = sum(1 for row in scored if row.get("success") is True)
        interval = wilson_interval(successes, len(scored))
        durations = [
            float(row["total_s"]) for row in scored if isinstance(row.get("total_s"), (int, float))
        ]
        tasks.append(
            {
                "task_id": task_id,
                "repeats": len(rows),
                "scored": len(scored),
                "successes": successes,
                "repeat_reliability": list(interval) if interval else None,
                "repeat_reliability_label": "repeat reliability",
                "duration_mean_s": _mean(durations),
            }
        )
        if scored:
            success_clusters.append([1.0 if row.get("success") else 0.0 for row in scored])
            if durations:
                duration_clusters.append(durations)
    errors = sum(1 for row in samples if row.get("status") == "error")
    skips = sum(1 for row in samples if row.get("status") == "skipped")
    cancelled = sum(1 for row in samples if row.get("status") in {"cancelled", "interrupted"})
    scored_rows = [row for row in samples if row.get("success") is not None]
    descriptive = adaptive
    suite = cluster_bootstrap(success_clusters, rng)
    if descriptive and suite["interval"] is not None:
        suite["label"] = "descriptive"
        suite["reason"] = (
            "adaptively stopped intervals are descriptive and are not regression evidence"
        )
    return {
        "independent_tasks": len([cluster for cluster in success_clusters if cluster]),
        "attempted": len(samples),
        "scored": len(scored_rows),
        "errors": errors,
        "skips": skips,
        "cancellations": cancelled,
        "coverage": (len(scored_rows) / len(samples)) if samples else None,
        "adaptive": adaptive,
        "confidence_label": "descriptive" if descriptive else "fixed-repeat",
        "tasks": tasks,
        "suite_success": suite,
        "method": "Wilson 95% for repeated binary task outcomes; task-cluster bootstrap for suites",
    }
