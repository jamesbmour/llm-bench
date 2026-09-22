from __future__ import annotations

import pytest

from llmsweep.errors import SelectionError
from llmsweep.models import ModelInfo, ModelRef, normalize_model, parse_ref, sort_models
from llmsweep.selection import parse_index_spec, resolve_scenarios, resolve_selection


def test_resolution_and_ranges() -> None:
    models = [ModelInfo(ModelRef("lmstudio", name)) for name in ["qwen-8b", "1", "qwen-3b"]]
    assert resolve_selection("1", models) == [models[1]]
    assert resolve_selection("3-1,2", models) == models
    assert parse_index_spec("3-1 2", 3) == [1, 2, 3]
    with pytest.raises(SelectionError, match=r"ambiguous.*qwen-8b.*qwen-3b"):
        resolve_selection("qwen", models)
    with pytest.raises(SelectionError, match=r"out of range: 9 \(valid: 1-3\)"):
        resolve_selection("9", models)
    assert parse_ref("llama3.2:latest") == ModelRef("lmstudio", "llama3.2:latest")
    assert parse_ref("lmstudio:qwen:8b") == ModelRef("lmstudio", "qwen:8b")


def test_metadata_not_names_and_size() -> None:
    chat = normalize_model(
        {"key": "nemotron-3-embed-1b", "type": "llm", "params_string": "7B"}, "v1"
    )
    embed = normalize_model({"id": "chat-9b", "type": "embeddings"}, "v0")
    assert chat.chat and not embed.chat
    assert chat.params_b == 7
    unknown = normalize_model({"key": "unknown"}, "v1")
    assert sort_models([unknown, chat]) == [chat, unknown]


def test_scenarios_order_and_errors() -> None:
    assert resolve_scenarios("codegen,weather,codegen") == ("codegen", "weather")
    with pytest.raises(SelectionError, match="valid: weather, agent-code, codegen"):
        resolve_scenarios("other")
