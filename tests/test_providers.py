from __future__ import annotations

import pytest

from llmsweep.errors import AuthenticationError, StreamError
from llmsweep.providers.lmstudio import LMStudio
from llmsweep.streams import TextDelta, ToolCallAccumulator, ToolCallDelta, UsageEvent
from tests.fakes.lmstudio import FakeLMStudio, calls, completion, no_wait


@pytest.mark.parametrize("version", ["v1", "v0"])
async def test_provider_contract(version: str) -> None:
    fake = FakeLMStudio(version)
    provider = LMStudio(transport=fake.transport, sleep=no_wait)
    try:
        models = await provider.list_models()
        chat = next(m for m in models if m.chat)
        assert sum(m.chat for m in models) == 1
        lease = await provider.acquire(chat, 5)
        fake.scripts = [calls(("get_weather", {"city": "Paris"}))]
        events = [e async for e in provider.chat(lease.chat_id, [], [], 100)]
        accumulator = ToolCallAccumulator()
        for event in events:
            if isinstance(event, ToolCallDelta):
                accumulator.add(event)
        assert accumulator.finish()[0].parsed_arguments() == {"city": "Paris"}
        events = [e async for e in provider.chat(lease.chat_id, [], [], 100)]
        assert any(isinstance(e, TextDelta) for e in events)
        assert any(isinstance(e, UsageEvent) and e.completion_tokens == 20 for e in events)
        await provider.release(lease)
        await provider.release(lease)
        assert not fake.loaded
        assert lease.load_s == (3.5 if version == "v1" else None)
    finally:
        await provider.close()


async def test_readiness_and_existing_instances() -> None:
    fake = FakeLMStudio()
    fake.load_pending = 2
    provider = LMStudio(transport=fake.transport, sleep=no_wait)
    try:
        model = next(m for m in await provider.list_models() if m.chat)
        lease = await provider.acquire(model, 5)
        assert fake.load_pending == 0 and fake.loaded
        assert lease.load_s == 3.5
        await provider.release(lease)
        fake.loaded["fixture"] = "preexisting"
        lease = await provider.acquire(model, 5)
        await provider.release(lease)
        assert fake.loaded == {"fixture": "preexisting"}
    finally:
        await provider.close()


async def test_retry_and_no_retry_after_delta() -> None:
    fake = FakeLMStudio()
    provider = LMStudio(transport=fake.transport, sleep=no_wait)
    try:
        fake.scripts = [500, completion("answer")]
        assert [e async for e in provider.chat("fixture", [], [], 100)]
        assert len(fake.chat_requests) == 2
        fake.scripts = [completion("partial")[:-1]]
        fake.disconnect = True
        with pytest.raises(StreamError):
            _ = [e async for e in provider.chat("fixture", [], [], 100)]
        assert len(fake.chat_requests) == 3
    finally:
        await provider.close()


async def test_authentication_never_retries() -> None:
    fake = FakeLMStudio()
    fake.scripts = [401, completion("not reached")]
    provider = LMStudio(transport=fake.transport, api_key="secret", sleep=no_wait)
    try:
        with pytest.raises(AuthenticationError):
            _ = [e async for e in provider.chat("fixture", [], [], 1)]
        assert len(fake.chat_requests) == 1
    finally:
        await provider.close()
