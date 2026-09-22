"""Shared model and scenario selection rules for CLI and TUI."""

from __future__ import annotations

import re

from .errors import SelectionError
from .models import ModelInfo, ModelRef, parse_ref

SCENARIOS = ("weather", "agent-code", "codegen")
INDEX = re.compile(r"\d+(?:-\d+)?\Z")


def parse_index_spec(spec: str, count: int) -> list[int]:
    result: list[int] = []
    for part in re.split(r"[,\s]+", spec.strip()):
        if not INDEX.fullmatch(part):
            raise SelectionError(f"invalid index expression: {part!r}")
        ends = [int(value) for value in part.split("-")]
        low, high = min(ends), max(ends)
        for endpoint in ends:
            if not 1 <= endpoint <= count:
                raise SelectionError(f"out of range: {endpoint} (valid: 1-{count})")
        result.extend(range(low, high + 1))
    return list(dict.fromkeys(result))


def resolve_selection(spec: str, models: list[ModelInfo]) -> list[ModelInfo]:
    by_ref = {model.ref: model for model in models}
    exact = by_ref.get(parse_ref(spec.strip()))
    if exact:
        return [exact]
    picked: dict[ModelRef, ModelInfo] = {}
    for part in re.split(r"[,\s]+", spec.strip()):
        exact = by_ref.get(parse_ref(part))
        if exact:
            matches = [exact]
        elif INDEX.fullmatch(part):
            matches = [models[index - 1] for index in parse_index_spec(part, len(models))]
        else:
            needle = parse_ref(part)
            matches = [
                m
                for m in models
                if m.ref.provider == needle.provider and needle.id.casefold() in m.ref.id.casefold()
            ]
            if not part or not matches:
                raise SelectionError(f"no model matches {part!r}")
            if len(matches) > 1:
                names = ", ".join(m.ref.key for m in matches)
                raise SelectionError(f"ambiguous model {part!r}: {names}")
        picked.update((model.ref, model) for model in matches)
    return list(picked.values())


def resolve_scenarios(spec: str) -> tuple[str, ...]:
    names = tuple(dict.fromkeys(part.strip() for part in spec.split(",")))
    invalid = [name for name in names if name not in SCENARIOS]
    if invalid:
        raise SelectionError(
            f"unknown scenario: {', '.join(invalid)}; valid: {', '.join(SCENARIOS)}"
        )
    return names


def filter_models(
    models: list[ModelInfo],
    *,
    tools_only: bool = False,
    exclude: str = "",
    strict: bool = False,
) -> list[ModelInfo]:
    needles = [part.strip().casefold() for part in exclude.split(",") if part.strip()]
    return [
        m
        for m in models
        if m.chat
        and (not tools_only or m.tool_use is True)
        and (not strict or m.type == "llm")
        and not any(needle in m.ref.id.casefold() for needle in needles)
    ]
