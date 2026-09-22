"""Provider-qualified model identity and LM Studio metadata normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import MalformedResponseError

REGISTERED_PROVIDERS = frozenset({"lmstudio"})
NON_CHAT_TYPES = frozenset({"embedding", "embeddings", "reranker"})
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([bBmM])\b")


@dataclass(frozen=True, order=True)
class ModelRef:
    """Stable identity qualified by its registered provider."""

    provider: str
    id: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.id}"


def parse_ref(value: str, provider: str = "lmstudio") -> ModelRef:
    prefix, sep, rest = value.partition(":")
    if sep and prefix in REGISTERED_PROVIDERS:
        return ModelRef(prefix, rest)
    return ModelRef(provider, value)


def size_billions(value: str) -> float | None:
    sizes = [float(n) / (1000 if unit.lower() == "m" else 1) for n, unit in SIZE_RE.findall(value)]
    return max(sizes) if sizes else None


@dataclass(frozen=True)
class ModelInfo:
    """Normalized model metadata with unknown capabilities kept distinct from false."""

    ref: ModelRef
    type: str = "unknown"
    params_b: float | None = None
    format: str | None = None
    quantization: str | None = None
    tool_use: bool | None = None
    vision: bool | None = None
    instances: tuple[str, ...] = ()
    loaded: bool = False

    @property
    def chat(self) -> bool:
        return self.type not in NON_CHAT_TYPES


def normalize_model(raw: dict[str, Any], version: str) -> ModelInfo:
    identifier = raw.get("key" if version == "v1" else "id")
    if not isinstance(identifier, str) or not identifier:
        raise MalformedResponseError("model has no valid identifier")
    kind = raw.get("type", "unknown")
    if not isinstance(kind, str):
        raise MalformedResponseError(f"invalid type for model {identifier}")
    caps = raw.get("capabilities")
    tool_use: bool | None = None
    vision: bool | None = None
    if isinstance(caps, dict):
        tool_use = caps.get("trained_for_tool_use")
        vision = caps.get("vision")
    elif isinstance(caps, list):
        tool_use = "tool_use" in caps or "tools" in caps
        vision = "vision" in caps
    if tool_use is not None and not isinstance(tool_use, bool):
        raise MalformedResponseError("tool capability must be a boolean")
    if vision is not None and not isinstance(vision, bool):
        raise MalformedResponseError("vision capability must be a boolean")
    instances = raw.get("loaded_instances") or []
    if not isinstance(instances, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in instances
    ):
        raise MalformedResponseError("loaded_instances must contain instance IDs")
    quant = raw.get("quantization")
    if isinstance(quant, dict):
        quant = quant.get("name")
    params = raw.get("params_string")
    size = size_billions(params) if isinstance(params, str) else None
    return ModelInfo(
        ref=ModelRef("lmstudio", identifier),
        type=kind.lower(),
        params_b=size if size is not None else size_billions(identifier),
        format=raw.get("format") if isinstance(raw.get("format"), str) else None,
        quantization=quant if isinstance(quant, str) else None,
        tool_use=tool_use,
        vision=vision,
        instances=tuple(item["id"] for item in instances),
        loaded=bool(instances) or raw.get("state") == "loaded",
    )


def sort_models(models: list[ModelInfo]) -> list[ModelInfo]:
    return sorted(models, key=lambda m: (-(m.params_b or -1), m.ref.id.casefold()))
