"""Provider protocol and explicit ownership of loaded instances."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from llmsweep.models import ModelInfo
from llmsweep.security import Redactor
from llmsweep.streams import StreamEvent


@dataclass
class Lease:
    """An instance reference recording whether this run owns its lifecycle."""

    model: ModelInfo
    instance_id: str | None = None
    we_loaded: bool = False
    load_s: float | None = None
    note: str = "already loaded"
    released: bool = False

    @property
    def chat_id(self) -> str:
        return self.instance_id or self.model.ref.id


class Provider(Protocol):
    """Async transport contract consumed by the renderer-independent runner."""

    redact: Redactor

    async def list_models(self) -> list[ModelInfo]: ...
    async def acquire(
        self, model: ModelInfo, load_deadline: float, *, allow_load: bool = True
    ) -> Lease: ...
    async def release(self, lease: Lease) -> None: ...
    async def close(self) -> None: ...
    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]: ...
