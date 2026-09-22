"""Versioned setup presets shared by the CLI and the TUI."""

from __future__ import annotations

from dataclasses import dataclass

from llmsweep.errors import ConfigError

ORIGINAL = ("weather", "agent-code", "codegen")
CODING = ("code-edge", "multi-file", "repo-issue")
FULL = (
    *ORIGINAL,
    "code-edge",
    "multi-file",
    "repo-issue",
    "constraint-plan",
    "knowledge-cal",
)


@dataclass(frozen=True, slots=True)
class Preset:
    name: str
    version: int
    label: str
    scenarios: tuple[str, ...]
    tasks: dict[str, tuple[str, ...]]


PRESETS: dict[str, Preset] = {
    "quick-check": Preset(
        "quick-check",
        1,
        "diagnostic",
        ("codegen", "code-edge"),
        {"code-edge": ("ce-01", "ce-02")},
    ),
    "coding-quality": Preset("coding-quality", 1, "evaluation", CODING, {}),
    "full-evaluation": Preset("full-evaluation", 1, "evaluation", FULL, {}),
}


def get_preset(name: str) -> Preset:
    try:
        return PRESETS[name]
    except KeyError:
        known = ", ".join(PRESETS)
        raise ConfigError(f"unknown preset {name!r}; valid: {known}") from None


def task_selection(preset: Preset) -> dict[str, tuple[str, ...]] | None:
    """None selects every task. An empty tasks map also selects every task."""
    if not preset.tasks:
        return None
    return dict(preset.tasks)


def format_tasks(preset: Preset) -> str:
    if not preset.tasks:
        return ""
    return ",".join(f"{suite}:{('|'.join(ids))}" for suite, ids in preset.tasks.items())


def plan_report(values: dict[str, object]) -> str:
    """Plain setup summary. Runtime estimates stay unknown until history exists."""
    from llmsweep.benchmarks.registry import tasks_for
    from llmsweep.execution.policy import isolated_policy, workflow_policy

    lines: list[str] = []
    preset_name = values.get("preset")
    if isinstance(preset_name, str) and preset_name:
        preset = get_preset(preset_name)
        lines.append(f"preset\t{preset.name}\t{preset.label}\t{preset.version}")
    else:
        lines.append("presets\t" + ",".join(PRESETS))
    raw_scenarios = values.get("scenarios")
    scenarios = raw_scenarios if isinstance(raw_scenarios, str) else ""
    lines.append(f"scenarios\t{scenarios}")
    if not values.get("pack"):
        raw_tasks = values.get("tasks")
        planned = tasks_for(
            tuple(part for part in scenarios.split(",") if part),
            parse_task_filter(raw_tasks if isinstance(raw_tasks, str) else None),
        )
        lines.append(f"tasks\t{len(planned)}")
        isolated = sorted({task.suite_id for task in planned if task.execution == "isolated"})
        lines.append(f"workflow\t{workflow_policy().detail}")
        status = isolated_policy()
        if isolated:
            state = "ready" if status.available else status.detail
            lines.append(f"isolated\t{','.join(isolated)}\t{state}")
    lines.append("eta\tunknown")
    return "\n".join(lines) + "\n"


def parse_task_filter(text: str | None) -> dict[str, tuple[str, ...]] | None:
    if not text:
        return None
    selected: dict[str, tuple[str, ...]] = {}
    for part in text.split(","):
        suite, sep, raw_ids = part.partition(":")
        if not sep:
            continue
        selected[suite] = tuple(task_id for task_id in raw_ids.split("|") if task_id)
    return selected or None
