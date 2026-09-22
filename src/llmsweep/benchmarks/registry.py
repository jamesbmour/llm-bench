"""Built-in suite registry. Lookup uses declared capabilities, not name special cases."""

from __future__ import annotations

from llmsweep.benchmarks.corpora.code_edge import code_edge_tasks
from llmsweep.benchmarks.corpora.constraint import constraint_tasks
from llmsweep.benchmarks.corpora.knowledge import knowledge_tasks
from llmsweep.benchmarks.corpora.repos import multi_file_tasks, repo_issue_tasks
from llmsweep.benchmarks.identity import fingerprint
from llmsweep.benchmarks.types import SuiteSpec, TaskSpec
from llmsweep.errors import SelectionError
from llmsweep.scenarios.agent_code import AgentCode
from llmsweep.scenarios.codegen import Codegen
from llmsweep.scenarios.weather import Weather


def _legacy(
    suite_id: str, title: str, prompt: str, *, tools: bool, turns: int, single: bool
) -> SuiteSpec:
    body = {"suite": suite_id, "prompt": prompt, "evaluator": suite_id + "/1"}
    task = TaskSpec(
        pack_id="builtin",
        pack_version="1",
        suite_id=suite_id,
        task_id=suite_id,
        prompt=prompt,
        evaluator_id=suite_id,
        evaluator_version="1",
        category="builtin",
        difficulty="medium",
        partition="eval",
        requires_tools=tools,
        execution="workflow",
        max_turns=turns,
        max_tokens=1024,
        task_seconds=300,
        content_digest=fingerprint(body),
        single_turn=single,
        tools=tuple(
            AgentCode.expected_tools
            if suite_id == "agent-code"
            else Weather.expected_tools
            if tools
            else ()
        ),
    )
    return SuiteSpec(
        suite_id=suite_id,
        version="1",
        title=title,
        requires_tools=tools,
        execution="workflow",
        languages=("python",),
        tasks=(task,),
    )


def builtin_suites() -> dict[str, SuiteSpec]:
    suites = [
        _legacy(
            "weather",
            "Weather tools",
            Weather.prompt,
            tools=True,
            turns=Weather.max_turns,
            single=False,
        ),
        _legacy(
            "agent-code",
            "Agent code repair",
            AgentCode.prompt,
            tools=True,
            turns=AgentCode.max_turns,
            single=False,
        ),
        _legacy("codegen", "Fibonacci codegen", Codegen.prompt, tools=False, turns=1, single=True),
        SuiteSpec(
            "code-edge",
            "1",
            "Edge-case code generation",
            False,
            "workflow",
            ("python",),
            code_edge_tasks(),
        ),
        SuiteSpec(
            "constraint-plan",
            "1",
            "Constraint planning",
            False,
            "workflow",
            ("python",),
            constraint_tasks(),
        ),
        SuiteSpec(
            "knowledge-cal",
            "1",
            "Knowledge calibration",
            False,
            "workflow",
            ("text",),
            knowledge_tasks(),
        ),
        SuiteSpec(
            "multi-file",
            "1",
            "Multi-file features",
            True,
            "isolated",
            ("python",),
            multi_file_tasks(),
        ),
        SuiteSpec(
            "repo-issue",
            "1",
            "Repository issues",
            True,
            "isolated",
            ("python",),
            repo_issue_tasks(),
        ),
    ]
    return {suite.suite_id: suite for suite in suites}


_SUITES: dict[str, SuiteSpec] | None = None


def suites() -> dict[str, SuiteSpec]:
    global _SUITES
    if _SUITES is None:
        _SUITES = builtin_suites()
    return _SUITES


def get_suite(name: str) -> SuiteSpec:
    try:
        return suites()[name]
    except KeyError:
        known = ", ".join(suites())
        raise SelectionError(f"unknown scenario: {name}; valid: {known}") from None


def tasks_for(
    names: tuple[str, ...], selected: dict[str, tuple[str, ...]] | None = None
) -> tuple[TaskSpec, ...]:
    chosen: list[TaskSpec] = []
    for name in names:
        suite = get_suite(name)
        allowed = None if selected is None else selected.get(name)
        for task in suite.tasks:
            if allowed is not None and task.task_id not in allowed:
                continue
            chosen.append(task)
    return tuple(chosen)


def find_task(suite_id: str, task_id: str) -> TaskSpec:
    suite = get_suite(suite_id)
    for task in suite.tasks:
        if task.task_id == task_id:
            return task
    raise SelectionError(f"unknown task {task_id} in {suite_id}")
