"""Comparison eligibility and dashboard view models. Renderers only display this output."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from llmsweep.metrics import regression_pct
from llmsweep.results import RunResult, compare_runs

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
            if target and target not in row["target"]:
                continue
            if benchmark and row["benchmark"] != benchmark:
                continue
            if task and row["task_id"] != task:
                continue
            if configuration and row["configuration"] != configuration:
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
                "success_rate": (successes / len(scored)) if scored else None,
                "task_duration_s": (sum(durations) / len(durations)) if durations else None,
                "tok_s": (sum(speeds) / len(speeds)) if speeds else None,
                "timing_comparable": len(fingerprints) <= 1
                and all(item is not None for item in fingerprints),
                "incomplete": any(row["status"] not in {"completed", "skipped"} for row in rows),
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
                metric: point[metric],
                "samples": point["samples"],
                "failures": point["failures"],
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
