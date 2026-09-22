from __future__ import annotations

from .agent_code import AgentCode
from .base import Scenario
from .codegen import Codegen
from .weather import Weather


def create_scenario(name: str, task: str | None = None) -> Scenario:
    if name == "weather":
        return Weather(task)
    if name == "agent-code":
        return AgentCode()
    if name == "codegen":
        return Codegen()
    raise ValueError(f"unknown scenario: {name}")
