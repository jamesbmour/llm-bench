from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from llmsweep import cli
from llmsweep.errors import AuthenticationError, ModelLoadError, StreamProtocolError
from llmsweep.providers.lmstudio import LMStudio
from llmsweep.runner import RunOptions, RunSession
from llmsweep.store import RunStore
from tests.fakes.lmstudio import FakeLMStudio, calls, completion, no_wait


async def test_auth_does_not_trigger_v0_fallback_and_redacts() -> None:
    requests: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(401, json={"error": "token secret-value rejected"})

    provider = LMStudio(api_key="secret-value", transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(AuthenticationError) as error:
            await provider.list_models()
        assert "secret-value" not in str(error.value)
        assert requests == ["/api/v1/models"]
    finally:
        await provider.close()


async def test_timeout_after_load_cleans_owned_instance() -> None:
    fake = FakeLMStudio()
    fake.load_pending = 100000

    async def expired_deadline(seconds: float) -> None:
        raise TimeoutError("scripted readiness deadline")

    provider = LMStudio(transport=fake.transport, sleep=expired_deadline)
    try:
        model = next(m for m in await provider.list_models() if m.chat)
        with pytest.raises(ModelLoadError):
            await provider.acquire(model, 600)
        assert not fake.pending and not fake.loaded
        assert any(r.url.path.endswith("/unload") for r in fake.requests)
    finally:
        await provider.close()


async def test_malformed_stream_does_not_silently_succeed() -> None:
    fake = FakeLMStudio()
    fake.scripts = [[b"data: {invalid}\n\n"]]
    provider = LMStudio(transport=fake.transport, sleep=no_wait)
    try:
        with pytest.raises(StreamProtocolError):
            _ = [event async for event in provider.chat("fixture", [], [], 10)]
        assert len(fake.chat_requests) == 1
    finally:
        await provider.close()


async def test_all_three_scenarios_score_and_use_fresh_workspaces(tmp_path: Path) -> None:
    from tests.test_runner import weather_script

    fake = FakeLMStudio()
    fake.scripts = [
        *weather_script(),
        calls(
            ("write_file", {"path": "buggy.py", "content": "def total(items):\n return sum(items)"})
        ),
        completion("Fixed it."),
        completion(
            "```python\ndef fib(n):\n a,b=0,1\n for _ in range(n): a,b=b,a+b\n return a\n```"
        ),
    ]
    provider = LMStudio(transport=fake.transport, sleep=no_wait)
    try:
        models = [m for m in await provider.list_models() if m.chat]
        session = RunSession(provider, RunStore(tmp_path), RunOptions(no_warmup=True))
        run = await session.run_all(models)
        assert [s.success for s in run.models[0].samples] == [True, True, True]
        assert run.models[0].summary()["success_rate"] == 1
        doc = run.document()
        assert all("token_source" in sample for sample in doc["models"][0]["samples"])
    finally:
        await provider.close()


async def test_unknown_and_malformed_tool_arguments_recorded(tmp_path: Path) -> None:
    fake = FakeLMStudio()
    fake.scripts = [
        calls(("unknown", {})),
        [
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"get_weather","arguments":"{"}}]}}]}\n\n',
            b"data: [DONE]\n\n",
        ],
        completion("incorrect"),
    ]
    provider = LMStudio(transport=fake.transport, sleep=no_wait)
    try:
        session = RunSession(
            provider, RunStore(tmp_path), RunOptions(no_warmup=True, scenarios=("weather",))
        )
        run = await session.run_all([m for m in await provider.list_models() if m.chat])
        sample = run.models[0].samples[0]
        assert sample.success is False
        assert len(sample.tool_errors) == 2
        assert run.exit_code() == 0
    finally:
        await provider.close()


def test_plain_error_output_and_transcripts_never_expose_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeLMStudio()
    original = fake.handle

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(400, text="secret-value")
        return original(request)

    def factory(*args: object, **kwargs: object) -> LMStudio:
        return LMStudio(
            api_key="secret-value", transport=httpx.MockTransport(handle), sleep=no_wait
        )

    monkeypatch.setattr(cli, "LMStudio", factory)
    assert (
        cli.main(
            [
                "--plain",
                "--all",
                "--no-warmup",
                "--run-store",
                str(tmp_path),
                "--api-key",
                "secret-value",
            ]
        )
        == 1
    )
    out = capsys.readouterr()
    assert "secret-value" not in out.out + out.err
    assert "\x1b" not in out.out + out.err
    for path in tmp_path.rglob("*.json"):
        assert "secret-value" not in path.read_text()
        json.loads(path.read_text())


async def test_parallel_does_not_load_models_that_disappear(tmp_path: Path) -> None:
    fake = FakeLMStudio(preloaded=True)
    provider = LMStudio(transport=fake.transport, sleep=no_wait)
    try:
        models = [m for m in await provider.list_models() if m.chat]
        fake.loaded.clear()
        session = RunSession(provider, RunStore(tmp_path), RunOptions(parallel=2, no_warmup=True))
        run = await session.run_all(models)
        assert run.exit_code() == 2
        assert not any(r.url.path.endswith("/load") for r in fake.requests)
        assert not fake.chat_requests
    finally:
        await provider.close()


def test_redaction_handles_json_escaping_and_ansi() -> None:
    from llmsweep.security import Redactor

    secret = 'sensitive"value'
    redact = Redactor(secret)
    assert secret not in redact(secret)
    assert json.loads(redact(json.dumps({"error": secret})))["error"] == "[REDACTED]"
    assert redact("\x1b[31merror\x1b[0m") == "error"
