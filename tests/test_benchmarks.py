from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from llmsweep import cli
from llmsweep.benchmarks.corpora.constraint import grade_assignment
from llmsweep.benchmarks.presets import get_preset, plan_report
from llmsweep.benchmarks.registry import get_suite, suites, tasks_for
from llmsweep.benchmarks.runtime import TaskScenario, grade_knowledge
from llmsweep.benchmarks.types import TaskSpec
from llmsweep.errors import ConfigError, ResumeError, SchemaVersionError
from llmsweep.models import ModelInfo, ModelRef
from llmsweep.packs import load_pack
from llmsweep.results import ModelResult
from llmsweep.statistics import cluster_bootstrap, reliability_report, wilson_interval
from llmsweep.store import RunLock, RunStore, canonical_json, load_run


def test_suite_counts_and_quick_check() -> None:
    counts = {name: suite.task_count for name, suite in suites().items()}
    assert counts["code-edge"] == 20
    assert counts["constraint-plan"] == 30
    assert counts["knowledge-cal"] == 100
    assert counts["multi-file"] == 6
    assert counts["repo-issue"] == 8
    knowledge = get_suite("knowledge-cal")
    assert sum(task.payload["abstain"] for task in knowledge.tasks) == 20
    preset = get_preset("quick-check")
    selected = tasks_for(preset.scenarios, dict(preset.tasks))
    assert [task.task_id for task in selected] == ["codegen", "ce-01", "ce-02"]
    report = plan_report(
        {
            "preset": "quick-check",
            "scenarios": "codegen,code-edge",
            "tasks": "code-edge:ce-01|ce-02",
            "pack": None,
        }
    )
    assert "tasks\t3" in report
    assert "eta\tunknown" in report


async def test_code_edge_reference_and_incorrect() -> None:
    for task in get_suite("code-edge").tasks:
        scenario = TaskScenario(task)
        try:
            good = await scenario.score_detail(str(task.payload["reference"]), [])
            bad = await scenario.score_detail(str(task.payload["incorrect"]), [])
        finally:
            scenario.close()
        assert good[0] is True, task.task_id
        assert bad[0] is False, task.task_id


def test_constraint_oracle_agrees_with_reference() -> None:
    for task in get_suite("constraint-plan").tasks:
        problem = task.payload["problem"]
        starts = task.payload["reference_starts"]
        if task.payload["solvable"]:
            passed, _ = grade_assignment(problem, {"feasible": True, "starts": starts})
            refused, _ = grade_assignment(problem, {"feasible": False, "starts": {}})
            assert passed is True
            assert refused is False
        else:
            passed, _ = grade_assignment(problem, {"feasible": False, "starts": {}})
            claimed, _ = grade_assignment(problem, {"feasible": True, "starts": {}})
            assert passed is True
            assert claimed is False


def test_knowledge_grades_answer_and_abstain() -> None:
    answerable = next(
        task for task in get_suite("knowledge-cal").tasks if not task.payload["abstain"]
    )
    abstain = next(task for task in get_suite("knowledge-cal").tasks if task.payload["abstain"])
    good = grade_knowledge(
        json.dumps({"answer": task_alias(answerable), "confidence": 0.9, "abstain": False}),
        answerable.payload,
    )
    missed = grade_knowledge(
        json.dumps({"answer": "not-the-fact", "confidence": 0.9, "abstain": False}),
        answerable.payload,
    )
    held = grade_knowledge(
        json.dumps({"answer": "", "confidence": 0.2, "abstain": True}),
        abstain.payload,
    )
    guessed = grade_knowledge(
        json.dumps({"answer": "Paris", "confidence": 0.9, "abstain": False}),
        abstain.payload,
    )
    assert good[0] is True
    assert missed[0] is False
    assert held[0] is True
    assert guessed[0] is False


def task_alias(task: TaskSpec) -> str:
    return str(task.payload["aliases"][0])


async def test_repo_reference_passes_and_public_defect_fails() -> None:
    task = get_suite("multi-file").tasks[0]
    fixed = TaskScenario(task)
    try:
        for name, content in task.payload["reference_files"].items():
            fixed.path(name).write_text(content, encoding="utf-8")
        passed = await fixed.score_detail("", ["write_file"])
    finally:
        fixed.close()
    broken = TaskScenario(task)
    try:
        failed = await broken.score_detail("", ["write_file"])
    finally:
        broken.close()
    assert passed[0] is True
    assert failed[0] is False


def test_pack_accepts_minimal_and_rejects_unsafe(tmp_path: Path) -> None:
    pack = load_pack(Path("examples/packs/minimal"))
    assert pack.pack_id == "minimal"
    assert pack.needs_isolation is False
    root = tmp_path / "pack"
    root.mkdir()
    (root / "pack.toml").write_text(
        'schema = 1\nid = "x"\nversion = "1"\n[[tasks]]\nid = "a"\nprompt = "p"\n'
        'evaluator = "exact"\nexpected = "a"\n[[tasks]]\nid = "a"\nprompt = "p"\n'
        'evaluator = "exact"\nexpected = "a"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="duplicate"):
        load_pack(root)
    (root / "pack.toml").write_text(
        'schema = 1\nid = "x"\nversion = "1"\n[[tasks]]\nid = "a"\nprompt = "p"\n'
        'evaluator = "shell"\nexpected = "a"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="not approved"):
        load_pack(root)
    (root / "pack.toml").write_text(
        'schema = 1\nid = "x"\nversion = "1"\n[[tasks]]\nid = "a"\nprompt = "p"\n'
        'evaluator = "exact"\nexpected = "a"\ncommand = "rm"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="forbidden"):
        load_pack(root)


def test_wilson_bounds_and_low_sample_bootstrap() -> None:
    perfect = wilson_interval(5, 5)
    failed = wilson_interval(0, 5)
    assert perfect is not None and failed is not None
    assert perfect[1] == 1
    assert failed[0] == 0
    assert perfect[0] > 0
    assert failed[1] < 1
    low = cluster_bootstrap([[1.0], [0.0]], random.Random(1))
    assert low["interval"] is None
    samples = [
        {
            "task_id": f"t{i}",
            "scenario": "code-edge",
            "status": "completed",
            "success": True,
            "total_s": 1.0,
        }
        for i in range(4)
    ]
    report = reliability_report(samples, adaptive=True, rng=random.Random("seed"))
    assert report["adaptive"] is True
    assert report["suite_success"]["interval"] is None


def test_schema_1_opens_without_rewrite(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = store.create({"scenarios": "weather"})
    run.models.append(ModelResult(ModelInfo(ModelRef("lmstudio", "fixture"))))
    raw = run.document()
    raw["schema_version"] = 1
    for key in ("schedule", "fingerprint", "provenance", "statistics", "task_manifest"):
        raw.pop(key, None)
    path = tmp_path / "legacy.json"
    path.write_text(canonical_json(raw), encoding="utf-8")
    before = path.read_bytes()
    loaded = load_run(path)
    assert path.read_bytes() == before
    assert loaded.schema_version == 1
    assert loaded.schedule is None
    path.write_text('{"schema_version": 99, "models": []}', encoding="utf-8")
    with pytest.raises(SchemaVersionError):
        load_run(path)


def test_cli_lists_benchmarks_and_setup(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["benchmarks", "list"]) == 0
    assert "knowledge-cal" in capsys.readouterr().out
    assert cli.main(["benchmarks", "validate", "examples/packs/minimal"]) == 0
    assert "minimal" in capsys.readouterr().out
    assert cli.main(["setup", "--preset", "quick-check"]) == 0
    assert "eta\tunknown" in capsys.readouterr().out
    assert cli.main(["run", "--provider", "ollama", "--all"]) == 2


def test_second_writer_is_rejected(tmp_path: Path) -> None:
    first = RunLock(tmp_path)
    first.acquire()
    try:
        with pytest.raises(ResumeError, match="locked"):
            RunLock(tmp_path).acquire()
    finally:
        first.release()
