from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from llmsweep.providers.lmstudio import sse


class FakeStream(httpx.AsyncByteStream):
    def __init__(
        self,
        chunks: list[bytes],
        gate: asyncio.Event | None = None,
        disconnect: bool = False,
        pause_after: int = 0,
    ) -> None:
        self.chunks = chunks
        self.gate = gate
        self.disconnect = disconnect
        self.pause_after = pause_after

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for index, chunk in enumerate(self.chunks):
            if self.gate and index >= self.pause_after:
                await self.gate.wait()
            yield chunk
        if self.disconnect:
            raise httpx.ReadError("scripted disconnect")


class FakeLMStudio:
    def __init__(self, version: str = "v1", *, preloaded: bool = False) -> None:
        self.version = version
        self.loaded = {"fixture": "preexisting"} if preloaded else {}
        self.requests: list[httpx.Request] = []
        self.chat_requests: list[dict[str, Any]] = []
        self.scripts: list[list[bytes] | int] = []
        self.disconnect = False
        self.gate: asyncio.Event | None = None
        self.load_pending = 0
        self.pending = False
        self.extra_models: list[str] = []
        self.pause_after = 0
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in ("/api/v1/models", "/api/v0/models"):
            if path == "/api/v1/models" and self.version == "v0":
                return httpx.Response(404, json={"error": "unsupported"})
            if self.pending:
                if self.load_pending == 0:
                    self.loaded["fixture"] = "owned"
                    self.pending = False
                else:
                    self.load_pending -= 1
            v1 = [
                {
                    "key": "fixture",
                    "type": "llm",
                    "params_string": "8B",
                    "capabilities": {"trained_for_tool_use": True},
                    "loaded_instances": [{"id": self.loaded["fixture"]}] if self.loaded else [],
                },
                {"key": "embedding", "type": "embedding"},
            ]
            v0 = [
                {
                    "id": "fixture",
                    "type": "llm",
                    "capabilities": ["tool_use"],
                    "state": "loaded" if self.loaded else "not-loaded",
                },
                {"id": "embedding", "type": "embeddings"},
            ]
            for name in self.extra_models:
                v1.insert(
                    -1,
                    {
                        "key": name,
                        "type": "llm",
                        "params_string": "3B",
                        "capabilities": {"trained_for_tool_use": True},
                        "loaded_instances": [{"id": self.loaded[name]}],
                    },
                )
            return httpx.Response(
                200, json={"models": v1} if self.version == "v1" else {"data": v0}
            )
        if path == "/api/v1/models/load":
            self.pending = True
            return httpx.Response(200, json={"instance_id": "owned", "load_time_seconds": 3.5})
        if path == "/api/v1/models/unload":
            payload = json.loads(request.content)
            assert payload["instance_id"] == "owned", "must not unload an existing instance"
            self.pending = False
            self.loaded.clear()
            return httpx.Response(200, json={"instance_id": "owned"})
        if path == "/v1/chat/completions":
            self.chat_requests.append(json.loads(request.content))
            script = self.scripts.pop(0) if self.scripts else completion("ok")
            if isinstance(script, int):
                return httpx.Response(script, json={"error": "scripted failure"})
            return httpx.Response(
                200,
                stream=FakeStream(script, self.gate, self.disconnect, self.pause_after),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(404)


def completion(text: str) -> list[bytes]:
    midpoint = max(1, len(text) // 2)
    return [
        sse({"choices": [{"delta": {"content": part}}]})
        for part in [text[:midpoint], text[midpoint:]]
    ] + [
        sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        sse(
            {
                "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            }
        ),
        b"data: [DONE]\n\n",
    ]


def calls(*items: tuple[str, dict[str, Any]]) -> list[bytes]:
    chunks = []
    for i, (name, args) in enumerate(items):
        raw = json.dumps(args)
        chunks.append(
            sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": i,
                                        "id": f"call_{i}",
                                        "function": {"name": name, "arguments": raw[:3]},
                                    }
                                ]
                            }
                        }
                    ]
                }
            )
        )
        chunks.append(
            sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"index": i, "function": {"arguments": raw[3:]}}]
                            }
                        }
                    ]
                }
            )
        )
    return [
        *chunks,
        sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n\n",
    ]


async def no_wait(seconds: float) -> None:
    return None
