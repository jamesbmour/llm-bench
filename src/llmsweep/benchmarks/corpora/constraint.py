"""Thirty small constraint problems with an independent exhaustive oracle."""

# Problem statements are data, not wrapped prose.
# ruff: noqa: E501

from __future__ import annotations

import json
from itertools import pairwise, product
from typing import Any

from llmsweep.benchmarks.identity import fingerprint
from llmsweep.benchmarks.types import TaskSpec


def exhaustive(problem: dict[str, Any]) -> bool:
    """True when some start assignment satisfies precedence, capacity, and horizon."""
    jobs = problem["jobs"]
    horizon = int(problem["horizon"])
    domains = []
    for job in jobs:
        latest = horizon - int(job["duration"])
        if latest < 0:
            return False
        domains.append(range(latest + 1))
    for starts in product(*domains):
        assignment = {job["id"]: start for job, start in zip(jobs, starts, strict=True)}
        if _feasible(problem, assignment):
            return True
    return False


def reference_assignment(problem: dict[str, Any]) -> dict[str, int] | None:
    jobs = problem["jobs"]
    horizon = int(problem["horizon"])
    domains = [range(horizon - int(job["duration"]) + 1) for job in jobs]
    for starts in product(*domains):
        assignment = {job["id"]: start for job, start in zip(jobs, starts, strict=True)}
        if _feasible(problem, assignment):
            return assignment
    return None


def grade_assignment(problem: dict[str, Any], answer: Any) -> tuple[bool, str]:
    if not isinstance(answer, dict) or "feasible" not in answer:
        return False, "invalid output: expected an object with feasible"
    claimed = answer.get("feasible")
    if not isinstance(claimed, bool):
        return False, "invalid output: feasible must be a boolean"
    starts = answer.get("starts", {})
    if claimed is False:
        if exhaustive(problem):
            return False, "false infeasibility claim"
        if starts not in ({}, None):
            return False, "infeasible answers must not include a schedule"
        return True, "infeasibility confirmed"
    if not isinstance(starts, dict):
        return False, "invalid output: starts must be an object"
    normalized: dict[str, int] = {}
    for job in problem["jobs"]:
        if job["id"] not in starts or isinstance(starts[job["id"]], bool):
            return False, f"missing start for {job['id']}"
        value = starts[job["id"]]
        if not isinstance(value, int):
            return False, f"start for {job['id']} must be an int"
        normalized[job["id"]] = value
    if not _feasible(problem, normalized):
        return False, "schedule violates a constraint"
    return True, "feasible schedule confirmed"


def _feasible(problem: dict[str, Any], starts: dict[str, int]) -> bool:
    jobs = {job["id"]: job for job in problem["jobs"]}
    horizon = int(problem["horizon"])
    for job_id, job in jobs.items():
        start = starts[job_id]
        if start < 0 or start + int(job["duration"]) > horizon:
            return False
    for before, after in problem["precedence"]:
        if starts[after] < starts[before] + int(jobs[before]["duration"]):
            return False
    by_machine: dict[str, list[tuple[int, int]]] = {}
    for job_id, job in jobs.items():
        span = (starts[job_id], starts[job_id] + int(job["duration"]))
        by_machine.setdefault(str(job["machine"]), []).append(span)
    for spans in by_machine.values():
        ordered = sorted(spans)
        for (left_start, left_end), (right_start, _right_end) in pairwise(ordered):
            if right_start < left_end or left_start == right_start:
                return False
    return True


def _problem(kind: str, index: int) -> dict[str, Any]:
    if kind == "scheduling":
        duration = 1 + (index % 3)
        return {
            "kind": kind,
            "horizon": 4 if index < 7 else 2,
            "jobs": [
                {"id": "a", "duration": duration, "machine": "m"},
                {"id": "b", "duration": 1, "machine": "m"},
            ],
            "precedence": [],
        }
    if kind == "dependency":
        return {
            "kind": kind,
            "horizon": 6 if index < 7 else 2,
            "jobs": [
                {"id": "a", "duration": 2, "machine": "m1"},
                {"id": "b", "duration": 2, "machine": "m2"},
                {"id": "c", "duration": 1, "machine": "m1"},
            ],
            "precedence": [["a", "c"], ["b", "c"]],
        }
    return {
        "kind": kind,
        "horizon": 5 if index < 7 else 2,
        "jobs": [
            {"id": "a", "duration": 2, "machine": "shared"},
            {"id": "b", "duration": 2, "machine": "shared"},
            {"id": "c", "duration": 1, "machine": "other"},
        ],
        "precedence": [],
    }


def constraint_tasks() -> tuple[TaskSpec, ...]:
    specs: list[TaskSpec] = []
    kinds = ("scheduling", "dependency", "resource")
    for offset, kind in enumerate(kinds):
        for index in range(10):
            problem = _problem(kind, index)
            solvable = exhaustive(problem)
            starts = reference_assignment(problem)
            task_id = f"cp-{offset * 10 + index + 1:02d}"
            prompt = (
                "Solve this constraint problem. Reply with JSON only: "
                '{"feasible": true, "starts": {"job": 0}} or {"feasible": false, "starts": {}}. '
                "starts maps each job id to an integer start time. Jobs on one machine cannot overlap. "
                "precedence pairs [before, after] require after to start once before has finished. "
                "Each job must finish at or before the horizon. Problem: "
                + json.dumps(problem, sort_keys=True)
            )
            body = {"problem": problem, "evaluator": "constraint-plan/1", "prompt": prompt}
            specs.append(
                TaskSpec(
                    pack_id="builtin",
                    pack_version="1",
                    suite_id="constraint-plan",
                    task_id=task_id,
                    prompt=prompt,
                    evaluator_id="constraint-plan",
                    evaluator_version="1",
                    category="reasoning",
                    difficulty="medium" if solvable else "hard",
                    partition="dev" if index < 2 else "eval",
                    requires_tools=False,
                    execution="workflow",
                    max_turns=1,
                    max_tokens=2048,
                    task_seconds=120,
                    content_digest=fingerprint(body),
                    single_turn=True,
                    payload={"problem": problem, "solvable": solvable, "reference_starts": starts},
                )
            )
    return tuple(specs)
