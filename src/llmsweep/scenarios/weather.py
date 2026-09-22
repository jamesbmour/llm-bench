from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .base import Scenario, tool


class Weather(Scenario):
    name = "weather"
    max_turns = 6
    expected_tools = ("get_weather", "convert_temperature", "get_current_time")
    prompt = (
        "Get the weather in Paris, convert its Celsius temperature to Fahrenheit, then get "
        "the time in Europe/Paris. Call all three tools. Finish with one sentence containing "
        "both temperatures and the time."
    )
    tools: ClassVar[list[dict[str, Any]]] = [
        tool("get_weather", "Get the current city weather", {"city": {"type": "string"}}),
        tool(
            "convert_temperature",
            "Convert a temperature",
            {
                "value": {"type": "number"},
                "from_unit": {"type": "string", "enum": ["c", "f", "k"]},
                "to_unit": {"type": "string", "enum": ["c", "f", "k"]},
            },
        ),
        tool(
            "get_current_time",
            "Get local time in an IANA timezone",
            {"timezone": {"type": "string"}},
        ),
    ]

    def __init__(self, task: str | None = None) -> None:
        super().__init__()
        self.task = task
        if task is not None:
            self.prompt = task

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "get_weather":
                city = args["city"]
                if not isinstance(city, str) or city.casefold() != "paris":
                    raise ValueError("only Paris weather is available")
                return {
                    "city": "Paris",
                    "temperature": 18.0,
                    "unit": "c",
                    "condition": "partly cloudy",
                }
            if name == "convert_temperature":
                value, source, target = args["value"], args["from_unit"], args["to_unit"]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    raise ValueError("value must be a finite number")
                if source not in ("c", "f", "k") or target not in ("c", "f", "k"):
                    raise ValueError("units must be c, f, or k")
                celsius = (
                    (value - 32) * 5 / 9
                    if source == "f"
                    else value - 273.15
                    if source == "k"
                    else value
                )
                result = (
                    celsius * 9 / 5 + 32
                    if target == "f"
                    else celsius + 273.15
                    if target == "k"
                    else celsius
                )
                return {"value": round(result, 2), "unit": target}
            if name == "get_current_time":
                zone = args["timezone"]
                return {
                    "timezone": zone,
                    "time": datetime.now(ZoneInfo(zone)).isoformat(timespec="seconds"),
                }
        except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError) as exc:
            return {"error": f"invalid {name} arguments: {exc}"}
        return await super().call(name, args)

    async def score(self, answer: str, called: list[str]) -> tuple[bool | None, str]:
        if self.task is not None:
            return None, "custom task: scoring disabled"
        passed = set(self.expected_tools).issubset(called) and bool(
            re.search(r"64(\.4)?\s*(°|deg(rees)?)?\s*f", answer, re.IGNORECASE)
        )
        return passed, "all three tools and 64.4 F required"
